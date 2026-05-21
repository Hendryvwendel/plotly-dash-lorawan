import threading
from flask import Flask, request
import pandas as pd
from datetime import datetime, timedelta
import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.express as px

# Deel 1. HTTP server om data te ontvangen van UG65
app_flask = Flask(__name__)
data_list = []

@app_flask.route("/loradatain", methods=["POST"])
def loradatain():
    payload = request.json
    raw_body = request.get_data(as_text=True)
    ts = datetime.now()
    # Verwacht JSON met (een van) de keys voor temperatuur, vochtigheid en CO2.
    # We proberen meerdere mogelijke sleutel-namen zodat het flexibeler is.
    def find_key(d, names):
        for n in names:
            if n in d:
                return d[n]
        return None

    temp = find_key(payload, ("temperature", "temp", "temperatuur"))
    hum = find_key(payload, ("humidity", "hum", "vochtigheid"))
    co2 = find_key(payload, ("co2", "CO2"))
    # Haal device identifier uit payload (flexibele naamgeving)
    device = find_key(payload, ("deviceName", "devicename", "device", "dev_id", "id", "name"))

    # Als payload één gecombineerde 'data' string bevat (raw), probeer die niet automatisch te ontleden
    # — liever expliciet aanpassen aan je gateway.

    # Convert to floats where possible
    def to_float(v):
        try:
            return float(v)
        except Exception:
            return None

    entry = {"timestamp": ts,
             "temperature": to_float(temp),
             "humidity": to_float(hum),
             "co2": to_float(co2),
             "deviceName": str(device) if device is not None else "unknown"}

    data_list.append(entry)
    print(f"[{ts:%Y-%m-%d %H:%M:%S}] Inkomend bericht:", payload, flush=True)
    if raw_body and raw_body != str(payload):
        print("Ruwe body:", raw_body, flush=True)
    return "OK", 200


@app_flask.route("/", methods=["GET"])
def index():
    return (
        "Flask server running. POST sensor data to /loradatain. "
        "Open the Dash UI at http://server-ip:8050/"
    ), 200

def run_flask():
    for port in (8000, 8001, 8002):
        try:
            print(f"Starting Flask on 0.0.0.0:{port}")
            app_flask.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
            break
        except OSError as e:
            print(f"Port {port} unavailable: {e}")
    else:
        print("Could not start Flask on ports 8000-8002")

# Deel 2. Dash-dashboard om data weer te geven
app_dash = dash.Dash(__name__)
app_dash.layout = html.Div([
    html.H3('Milesight UG65 Live Data'),
    dcc.Dropdown(
        id="metric-select",
        options=[
            {"label": "All", "value": "all"},
            {"label": "Temperature", "value": "temperature"},
            {"label": "Humidity", "value": "humidity"},
            {"label": "CO2", "value": "co2"},
        ],
        value="all",
        clearable=False,
        style={"width": "250px"}
    ),
    # Drie grafieken onder elkaar (temperatuur, humidity, CO2)
    html.Div(id="graphs-container", style={"display": "flex", "flexDirection": "column", "gap": "1rem", "marginTop": "1rem"}, children=[
        dcc.Graph(id="live-fig-temp"),
        dcc.Graph(id="live-fig-hum"),
        dcc.Graph(id="live-fig-co2"),
    ]),
    html.Div(id="latest-values", style={"marginTop": "1rem", "fontSize": "16px"}),
    dcc.Interval(interval=3*1000, n_intervals=0, id="interval")
])

@app_dash.callback(
    [
        Output("live-fig-temp", "figure"), Output("live-fig-temp", "style"),
        Output("live-fig-hum", "figure"), Output("live-fig-hum", "style"),
        Output("live-fig-co2", "figure"), Output("live-fig-co2", "style"),
        Output("latest-values", "children"),
    ],
    [Input("interval", "n_intervals"), Input("metric-select", "value")]
)
def update_graph(_n_intervals, selected_metric):
    empty_fig = px.line(title="")

    if not data_list:
        fig_no_data = px.line(title="Nog geen data ontvangen")
        if selected_metric == "all":
            empty_children = html.Div([
                html.Div("Temperature: 00, 00 °C"),
                html.Div("Humidity: 000, 00 %"),
                html.Div("CO2: 000, 00 ppm"),
            ])
            style_show = {"display": "block"}
            return fig_no_data, style_show, fig_no_data, style_show, fig_no_data, style_show, empty_children
        else:
            label = selected_metric.capitalize() if selected_metric else "Value"
            empty_children = html.Div([html.Div(f"{label}: -")])
            # show only selected metric, hide others
            style_hidden = {"display": "none"}
            style_show = {"display": "block"}
            if selected_metric == "temperature":
                return fig_no_data, style_show, empty_fig, style_hidden, empty_fig, style_hidden, empty_children
            if selected_metric == "humidity":
                return empty_fig, style_hidden, fig_no_data, style_show, empty_fig, style_hidden, empty_children
            return empty_fig, style_hidden, empty_fig, style_hidden, fig_no_data, style_show, empty_children

    df = pd.DataFrame(data_list)
    # Zorg dat timestamps correct zijn
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # Filter: alleen de laatste 10 minuten tonen
    now = datetime.now()
    cutoff = now - timedelta(minutes=10)
    df = df[df["timestamp"] >= cutoff]

    # Maak lange vorm voor meerdere series
    value_cols = [c for c in ("temperature", "humidity", "co2") if c in df.columns]
    if not value_cols:
        fig = px.line(title="Geen meetwaarden gevonden in data")
        empty_children = html.Div([
            html.Div("Temperature: 00, 00 *c"),
            html.Div("Humidity: 000, 00 %"),
            html.Div("CO2: 000, 00 ppm"),
        ])
        return empty_fig, fig, fig, fig, empty_children

    # Zorg dat deviceName (indien aanwezig) wordt meegenomen zodat we per device kunnen plotten
    id_vars = ["timestamp"]
    if "deviceName" in df.columns:
        df["deviceName"] = df["deviceName"].astype(str)
        id_vars.append("deviceName")
    df_long = df.melt(id_vars=id_vars, value_vars=value_cols, var_name="metric", value_name="value")
    df_long["value"] = pd.to_numeric(df_long["value"], errors="coerce")

    # Default ranges when no sensible range can be calculated
    default_ranges = {
        "temperature": [10, 30],
        "humidity": [30, 70],
        "co2": [500, 1500],
    }

    # Calculate y-axis range with 5% padding
    def calc_range(data_values):
        valid_vals = data_values.dropna()
        if valid_vals.empty:
            return None
        vmin, vmax = valid_vals.min(), valid_vals.max()
        if vmin == vmax:
            padding = abs(vmin) * 0.1 if vmin != 0 else 0.5
            return [vmin - padding, vmax + padding]
        padding = (vmax - vmin) * 0.05
        return [vmin - padding, vmax + padding]

    # Generate individual metric figures for "all" view
    def make_metric_fig(metric_name):
        df_metric = df_long[df_long["metric"] == metric_name]
        if df_metric.empty:
            fig_empty = px.line(title=f"Geen {metric_name} data")
            fig_empty.update_layout(yaxis_autorange=False, yaxis_range=default_ranges.get(metric_name), showlegend=False)
            return fig_empty
        # Wanneer deviceName aanwezig is, kleur lijnen per device
        kwargs = {"color": "deviceName"} if "deviceName" in df_metric.columns else {}
        fig_m = px.line(df_metric, x="timestamp", y="value", title=f"{metric_name.capitalize()}", markers=True, **kwargs)
        y_range = calc_range(df_metric["value"]) or default_ranges.get(metric_name)
        # If calc_range returns very small span, fall back to default
        if y_range and (y_range[1] - y_range[0]) < 0.1:
            y_range = default_ranges.get(metric_name)
        fig_m.update_layout(
            yaxis_autorange=False,
            yaxis_range=y_range,
            showlegend=True,
            legend=dict(title="Device", orientation="v", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        return fig_m

    # Filter by selected metric if requested
    if selected_metric and selected_metric != "all":
        df_plot = df_long[df_long["metric"] == selected_metric]
        title = f"{selected_metric.capitalize()} over tijd"
        kwargs_top = {"color": "deviceName"} if "deviceName" in df_plot.columns else {}
        fig_top = px.line(df_plot, x="timestamp", y="value", title=title, markers=True, **kwargs_top)
        y_range = calc_range(df_plot["value"]) or default_ranges.get(selected_metric)
        if y_range and (y_range[1] - y_range[0]) < 0.1:
            y_range = default_ranges.get(selected_metric)
        fig_top.update_layout(
            yaxis_autorange=False,
            yaxis_range=y_range,
            showlegend=True,
            legend=dict(title="Device", orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )

        # Show selected metric on top, others empty
        if selected_metric == "temperature":
            fig_temp = fig_top
            fig_hum = empty_fig
            fig_co2 = empty_fig
            style_temp = {"display": "block"}
            style_hum = {"display": "none"}
            style_co2 = {"display": "none"}
        elif selected_metric == "humidity":
            fig_temp = empty_fig
            fig_hum = fig_top
            fig_co2 = empty_fig
            style_temp = {"display": "none"}
            style_hum = {"display": "block"}
            style_co2 = {"display": "none"}
        else:
            fig_temp = empty_fig
            fig_hum = empty_fig
            fig_co2 = fig_top
            style_temp = {"display": "none"}
            style_hum = {"display": "none"}
            style_co2 = {"display": "block"}
    else:
        # "All" view: three separate graphs stacked
        fig_temp = make_metric_fig("temperature")
        fig_hum = make_metric_fig("humidity")
        fig_co2 = make_metric_fig("co2")
        style_temp = {"display": "block"}
        style_hum = {"display": "block"}
        style_co2 = {"display": "block"}

    # Laat de laatste gemeten waarden zien
    def last_value(col):
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce").dropna()
            if not s.empty:
                return s.iloc[-1]
        return None

    tv = last_value("temperature")
    hv = last_value("humidity")
    cv = last_value("co2")

    if selected_metric and selected_metric != "all":
        val_map = {"temperature": tv, "humidity": hv, "co2": cv}
        v = val_map.get(selected_metric)
        label = selected_metric.capitalize()
        children = html.Div([html.Div(f"{label}: {v if v is not None else '-'}")])
    else:
        lines = []
        lines.append(html.Div(f"Temperature: {tv if tv is not None else '00, 00 °C'}"))
        lines.append(html.Div(f"Humidity: {hv if hv is not None else '000, 00 %'}"))
        lines.append(html.Div(f"CO2: {cv if cv is not None else '000, 00 ppm'}"))
        children = html.Div(lines)

    return fig_temp, style_temp, fig_hum, style_hum, fig_co2, style_co2, children

def run_dash():
    # Use the newer `run` API (replaces deprecated `run_server`).
    try:
        app_dash.run(host="0.0.0.0", port=8050, debug=True, use_reloader=False)
    except TypeError:
        # Fallback in case the environment still expects run_server
        app_dash.run_server(host="0.0.0.0", port=8050, debug=True, use_reloader=False)

# Start beide servers tegelijk
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    run_dash()

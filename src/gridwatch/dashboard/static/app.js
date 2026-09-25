/* Renders the dashboard charts and their table views. Colours come from CSS custom
   properties so the light and dark themes each use their own palette steps. */
(function () {
  "use strict";

  var charts = JSON.parse(document.getElementById("chart-data").textContent);

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function withAlpha(hex, alpha) {
    var h = hex.replace("#", "");
    if (h.length === 3) {
      h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    }
    var n = parseInt(h, 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
  }

  function roleColour(role) {
    if (role === "muted") {
      return token("--muted");
    }
    return token("--" + role) || token("--series-1");
  }

  function themed(spec) {
    var data = spec.data.map(function (trace) {
      var copy = JSON.parse(JSON.stringify(trace));
      var meta = copy.meta || {};
      var colour = roleColour(meta.role || "series-1");
      if (meta.role === "heat") {
        copy.colorscale = [
          [0, token("--heat-low")],
          [0.5, token("--heat-mid")],
          [1, token("--heat-high")]
        ];
        copy.colorbar = Object.assign({}, copy.colorbar, {
          tickfont: { color: token("--text-secondary") },
          title: Object.assign({}, (copy.colorbar || {}).title, {
            font: { color: token("--text-secondary") }
          }),
          outlinewidth: 0
        });
      } else if (copy.type === "bar") {
        copy.marker = Object.assign({}, copy.marker, { color: colour });
        copy.textfont = { color: token("--text-secondary") };
      } else if (meta.band) {
        copy.line = Object.assign({}, copy.line, { color: colour, width: 0 });
        copy.fillcolor = withAlpha(colour, 0.14);
      } else {
        copy.line = Object.assign({ width: 2 }, copy.line, { color: colour });
      }
      return copy;
    });
    var axis = {
      gridcolor: token("--grid"),
      linecolor: token("--axis"),
      zerolinecolor: token("--axis"),
      tickfont: { color: token("--text-muted") },
      title: { font: { color: token("--text-secondary") } },
      automargin: true
    };
    var layout = JSON.parse(JSON.stringify(spec.layout));
    layout.paper_bgcolor = "rgba(0,0,0,0)";
    layout.plot_bgcolor = "rgba(0,0,0,0)";
    layout.font = { family: "system-ui, -apple-system, Segoe UI, sans-serif", size: 12,
                    color: token("--text-secondary") };
    layout.hoverlabel = { bgcolor: token("--surface"), bordercolor: token("--axis"),
                          font: { color: token("--text-primary") } };
    layout.xaxis = mergeAxis(layout.xaxis, axis);
    layout.yaxis = mergeAxis(layout.yaxis, axis);
    if (layout.legend) {
      layout.legend.font = { color: token("--text-secondary") };
    }
    return { data: data, layout: layout };
  }

  function mergeAxis(existing, theme) {
    var out = Object.assign({}, theme, existing || {});
    var title = (existing && existing.title) || {};
    out.title = Object.assign({}, title, { font: theme.title.font });
    out.gridcolor = theme.gridcolor;
    out.linecolor = theme.linecolor;
    out.tickfont = theme.tickfont;
    return out;
  }

  var config = { displayModeBar: false, responsive: true };

  function renderAll() {
    charts.forEach(function (chart) {
      if (!chart.spec) {
        return;
      }
      var el = document.getElementById("chart-" + chart.id);
      if (!el || typeof Plotly === "undefined") {
        return;
      }
      var spec = themed(chart.spec);
      Plotly.react(el, spec.data, spec.layout, config);
    });
  }

  function buildTables() {
    charts.forEach(function (chart) {
      var holder = document.querySelector('[data-table="' + chart.id + '"]');
      if (!holder || !chart.rows.length) {
        return;
      }
      var table = document.createElement("table");
      var head = table.createTHead().insertRow();
      chart.columns.forEach(function (name) {
        var th = document.createElement("th");
        th.scope = "col";
        th.textContent = name;
        head.appendChild(th);
      });
      var body = table.createTBody();
      chart.rows.forEach(function (row) {
        var tr = body.insertRow();
        row.forEach(function (value) {
          var td = tr.insertCell();
          if (value === null || value === undefined) {
            td.textContent = "";
          } else if (typeof value === "number") {
            td.textContent = value.toLocaleString("en-GB", { maximumFractionDigits: 2 });
          } else {
            td.textContent = String(value);
          }
        });
      });
      holder.appendChild(table);
    });
  }

  buildTables();
  renderAll();
  if (window.matchMedia) {
    var query = window.matchMedia("(prefers-color-scheme: dark)");
    if (query.addEventListener) {
      query.addEventListener("change", renderAll);
    }
  }
  new MutationObserver(renderAll).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"]
  });
})();

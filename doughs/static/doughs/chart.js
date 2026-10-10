// Draws the dough/ambient temperature chart, refreshing it every 30s while
// the dough is still collecting readings.
(function () {
  const canvas = document.getElementById('readings-chart');
  if (!canvas) {
    return;
  }

  const timeOnly = { hour: '2-digit', minute: '2-digit' };

  const chart = new Chart(canvas, {
    type: 'line',
    data: {
      datasets: [
        { label: 'Dough °C', data: [], yAxisID: 'temperature' },
        { label: 'Ambient °C', data: [], yAxisID: 'temperature' },
        { label: 'Humidity %', data: [], yAxisID: 'humidity', hidden: true },
      ],
    },
    options: {
      animation: false,
      elements: { point: { radius: 0 } },
      interaction: { mode: 'nearest', axis: 'x', intersect: false },
      scales: {
        x: {
          type: 'linear',
          ticks: { callback: (value) => new Date(value).toLocaleTimeString([], timeOnly) },
        },
        temperature: { position: 'left', title: { display: true, text: '°C' } },
        humidity: {
          position: 'right',
          min: 0,
          max: 100,
          title: { display: true, text: '% RH' },
          grid: { drawOnChartArea: false },
        },
      },
      plugins: {
        tooltip: {
          callbacks: { title: (items) => new Date(items[0].parsed.x).toLocaleString() },
        },
      },
    },
  });

  async function refresh() {
    const response = await fetch(canvas.dataset.url);
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    chart.data.datasets[0].data = data.dough;
    chart.data.datasets[1].data = data.ambient;
    chart.data.datasets[2].data = data.humidity;
    chart.update();
  }

  refresh();
  if (canvas.dataset.collecting === 'true') {
    setInterval(refresh, 30000);
  }
})();

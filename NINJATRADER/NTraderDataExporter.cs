#region Using declarations
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class NTraderDataExporter : Strategy
    {
        private readonly string exportPath = @"C:\NTraderSystem\backtest\data";

        // 5m is now an EXPLICIT added series (IDX 1), so the export no longer
        // depends on the chart's timeframe. Attach the strategy to any chart
        // (a higher TF like 1h/Daily is fine) and the four series are exported
        // deterministically. Load a wide date range / high "Days to load" so the
        // intraday series cover the full history (e.g. 2021 -> present).
        private const int IDX_5M  = 1;
        private const int IDX_15M = 2;
        private const int IDX_1H  = 3;
        private const int IDX_4H  = 4;

        private bool exported = false;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "NTraderDataExporter";
                Description = "Exporta datos históricos OHLCV para backtesting";
                Calculate = Calculate.OnBarClose;
                BarsRequiredToTrade = 0;
            }
            else if (State == State.Configure)
            {
                AddDataSeries(BarsPeriodType.Minute, 5);
                AddDataSeries(BarsPeriodType.Minute, 15);
                AddDataSeries(BarsPeriodType.Minute, 60);
                AddDataSeries(BarsPeriodType.Minute, 240);
            }
            else if (State == State.DataLoaded)
            {
                Directory.CreateDirectory(exportPath);
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0)
                return;

            if (exported)
                return;

            // Solo exportar cuando todas las series tengan datos suficientes
            if (CurrentBars[0] < 20 || CurrentBars[IDX_5M] < 50 ||
                CurrentBars[IDX_15M] < 50 || CurrentBars[IDX_1H] < 50 ||
                CurrentBars[IDX_4H] < 20)
                return;

            // Exportar solo una vez, al llegar a la última barra histórica
            if (State == State.Historical && CurrentBar < BarsArray[0].Count - 2)
                return;

            try
            {
                string instrument = Instrument.MasterInstrument.Name;
                Print("EXPORTER -> Exportando datos para " + instrument);

                ExportSeries(instrument, "5m",  BarsArray[IDX_5M]);
                ExportSeries(instrument, "15m", BarsArray[IDX_15M]);
                ExportSeries(instrument, "1h",  BarsArray[IDX_1H]);
                ExportSeries(instrument, "4h",  BarsArray[IDX_4H]);

                exported = true;
                Print("EXPORTER -> Exportación completada para " + instrument);
            }
            catch (Exception ex)
            {
                Print("EXPORTER -> ERROR: " + ex.Message);
            }
        }

        private void ExportSeries(string instrument, string timeframe, NinjaTrader.Data.Bars bars)
        {
            string fileName = instrument + "_" + timeframe + ".csv";
            string filePath = Path.Combine(exportPath, fileName);
            string tempPath = filePath + ".tmp";

            var sb = new StringBuilder();
            sb.AppendLine("DateTime,Open,High,Low,Close,Volume");

            for (int i = 0; i < bars.Count; i++)
            {
                DateTime dt = bars.GetTime(i);
                double open  = bars.GetOpen(i);
                double high  = bars.GetHigh(i);
                double low   = bars.GetLow(i);
                double close = bars.GetClose(i);
                double vol   = bars.GetVolume(i);

                sb.AppendLine(string.Format(CultureInfo.InvariantCulture,
                    "{0:yyyy-MM-dd HH:mm:ss},{1},{2},{3},{4},{5}",
                    dt, open, high, low, close, vol));
            }

            using (var fs = new FileStream(tempPath, FileMode.Create, FileAccess.Write, FileShare.Read))
            using (var sw = new StreamWriter(fs, Encoding.UTF8))
                sw.Write(sb.ToString());

            if (File.Exists(filePath)) File.Delete(filePath);
            File.Move(tempPath, filePath);
            Print("EXPORTER -> " + fileName + " | Barras: " + bars.Count);
        }
    }
}

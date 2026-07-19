#region Using declarations
using System;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class NTraderUnifiedBridge : Strategy
    {
        private DateTime lastPollUtc     = Core.Globals.MinDate;
        private DateTime lastBarExportUtc = Core.Globals.MinDate;
        private DateTime lastHeartbeatUtc = Core.Globals.MinDate;
        private const double HEARTBEAT_INTERVAL_SEC = 15.0;

        // Marca de tiempo del ULTIMO tick real recibido (OnBarUpdate de la serie
        // primaria en Realtime). El timer escribe el heartbeat aunque no haya
        // ticks, asi que el mtime del archivo solo prueba que el PROCESO esta vivo.
        // dataAgeSec (UtcNow - lastTickUtc) distingue proceso-vivo de datos-vivos:
        // si el feed se cae pero la estrategia sigue corriendo no entran ticks,
        // lastTickUtc deja de avanzar y dataAgeSec crece -> NTEXECG sabe que los
        // datos estan rancios aunque el heartbeat siga fresco.
        private DateTime lastTickUtc = Core.Globals.MinDate;

        // Timer independiente del flujo de ticks/barras.
        // Motivo: si OnBarUpdate deja de dispararse, el bridge deja de exportar OHLC/heartbeat.
        private System.Timers.Timer bridgeTimer = null;
        private bool bridgeWorkInProgress = false;
        private const double BRIDGE_TIMER_INTERVAL_MS = 1000.0;

        // Índices de las series adicionales (0 = primary 5m)
        private const int IDX_15M = 1;
        private const int IDX_1H  = 2;
        private const int IDX_4H  = 3;

        // Barras a exportar por timeframe
        private const int    BARS_5M              = 300;  // ~25 horas de datos en 5m
        private const int    BARS_15M             = 200;  // ~50 horas en 15m
        private const int    BARS_1H              = 250;  // ~250 horas → SMA200 disponible
        private const int    BARS_4H              = 90;   // ~360 horas en 4h
        private const double BAR_EXPORT_INTERVAL_SEC = 10.0;

        private readonly string inPath = @"C:\NTEXECGSystem\Bridge\in";
        private readonly string outPath = @"C:\NTEXECGSystem\Bridge\out";
        private readonly string processedPath = @"C:\NTEXECGSystem\Bridge\processed";
        private readonly string errorPath = @"C:\NTEXECGSystem\Bridge\error";

        // =====================================================================
        // HOLC UNIFICADO — historia completa {SYM}_{tf}.csv en Bridge\out
        // (viaja al server por el share ya montado en /mnt/ntbridge).
        // REQUISITOS DEL CHART: timezone del EXCHANGE (ET) y "Days to load"
        // con el rango historico deseado (2-3 anos). La primaria es 5m.
        // Ritual: al pasar a Realtime se reescriben COMPLETOS los 4 CSV
        // (auto-sana huecos de la noche); durante la jornada cada barra
        // cerrada se APPENDEA; opcionalmente re-export completo cada N horas.
        // =====================================================================
        private const bool   HOLC_EXPORT_ENABLED          = true;
        private const double HOLC_FULL_REEXPORT_HOURS     = 4.0;   // 0 = solo al activar
        private bool     holcFullExportPending = false;
        private DateTime lastHolcFullUtc       = Core.Globals.MinDate;
        private readonly System.Collections.Generic.Queue<int> holcQueue
            = new System.Collections.Generic.Queue<int>();
        private readonly bool[]     holcReady       = new bool[4];
        private readonly DateTime[] lastHolcBarTime = new DateTime[4];
        private static readonly string[] HOLC_TF = { "5m", "15m", "1h", "4h" };


        // ============================
        // Contexto activo actual
        // ============================
        private string currentClientOrderId = string.Empty;
        private string currentStrategyTag = string.Empty;
        private string currentSignalType = string.Empty;
        private string currentInstrument = string.Empty;

        private string currentEntryOrderId = string.Empty;
        private string currentStopOrderId = string.Empty;
        private string currentTargetOrderId = string.Empty;
        private string currentExitOrderId = string.Empty;

        private int currentRequestedQuantity = 0;
        private double currentRequestedStopLoss = 0.0;
        private double currentRequestedTakeProfit = 0.0;
        private bool currentIsExit = false;

        private double? currentActiveStopPrice = null;
        private double? currentActiveTargetPrice = null;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "NTraderUnifiedBridge";
                Description = "Bridge de ejecucion + exportador HOLC unificado (historia completa al activar, append por barra cerrada, re-export de sanacion)";
                Calculate = Calculate.OnEachTick;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;
                ExitOnSessionCloseSeconds = 30;
                StartBehavior = StartBehavior.WaitUntilFlat;
                TimeInForce = NinjaTrader.Cbi.TimeInForce.Gtc;
                RealtimeErrorHandling = RealtimeErrorHandling.IgnoreAllErrors;
                StopTargetHandling = StopTargetHandling.PerEntryExecution;
                BarsRequiredToTrade = 0;

                Print("BRIDGE -> SetDefaults");
            }
            else if (State == State.Configure)
            {
                Print("BRIDGE -> Configure");
                EnsureDirectories();

                // Series adicionales para exportar barras multi-timeframe
                // La serie primaria (índice 0) es la del chart donde se ejecuta la estrategia (5m)
                AddDataSeries(Data.BarsPeriodType.Minute, 15);  // IDX_15M = 1
                AddDataSeries(Data.BarsPeriodType.Minute, 60);  // IDX_1H  = 2
                AddDataSeries(Data.BarsPeriodType.Minute, 240); // IDX_4H  = 3
            }
            else if (State == State.DataLoaded)
            {
                Print("BRIDGE -> DataLoaded");
            }
            else if (State == State.Historical)
            {
                Print("BRIDGE -> Historical");
            }
            else if (State == State.Transition)
            {
                Print("BRIDGE -> Transition");
            }
            else if (State == State.Realtime)
            {
                Print("BRIDGE -> Realtime");
                holcFullExportPending = HOLC_EXPORT_ENABLED;   // HOLC: historia completa al activar
                StartBridgeTimer();
            }
            else if (State == State.Terminated)
            {
                StopBridgeTimer();
                Print("BRIDGE -> Terminated");
            }
        }

        protected override void OnBarUpdate()
        {
            // HOLC: al PRIMER tick de una barra nueva, la barra [1] de esa serie
            // acaba de cerrar -> se appendea al CSV historico correspondiente.
            if (HOLC_EXPORT_ENABLED && State == State.Realtime
                && BarsInProgress >= 0 && BarsInProgress <= 3
                && IsFirstTickOfBar && CurrentBars[BarsInProgress] > 0)
            {
                try { HolcAppendClosedBar(BarsInProgress); }
                catch (Exception ex) { Print("BRIDGE -> HOLC APPEND ERROR: " + ex.Message); }
            }

            if (BarsInProgress != 0)
                return;

            if (State != State.Realtime)
                return;

            // Mantener OnBarUpdate como disparador adicional, pero ya no depender de él.
            // El timer independiente también llama BridgeTick().
            // Solo los ticks reales actualizan lastTickUtc (no el timer).
            lastTickUtc = DateTime.UtcNow;
            BridgeTick("OnBarUpdate");
        }

        private void StartBridgeTimer()
        {
            try
            {
                StopBridgeTimer();

                bridgeTimer = new System.Timers.Timer(BRIDGE_TIMER_INTERVAL_MS);
                bridgeTimer.AutoReset = true;
                bridgeTimer.Elapsed += OnBridgeTimerElapsed;
                bridgeTimer.Start();

                Print("BRIDGE -> Timer started");
            }
            catch (Exception ex)
            {
                Print("BRIDGE -> ERROR starting timer: " + ex.Message);
            }
        }

        private void StopBridgeTimer()
        {
            try
            {
                if (bridgeTimer != null)
                {
                    bridgeTimer.Stop();
                    bridgeTimer.Elapsed -= OnBridgeTimerElapsed;
                    bridgeTimer.Dispose();
                    bridgeTimer = null;
                }
            }
            catch (Exception ex)
            {
                Print("BRIDGE -> ERROR stopping timer: " + ex.Message);
            }
        }

        private void OnBridgeTimerElapsed(object sender, System.Timers.ElapsedEventArgs e)
        {
            if (State != State.Realtime)
                return;

            try
            {
                // Evita tocar objetos NinjaScript desde el hilo del timer.
                // TriggerCustomEvent agenda el trabajo dentro del contexto de la estrategia.
                TriggerCustomEvent(o => BridgeTick("Timer"), null);
            }
            catch (Exception ex)
            {
                Print("BRIDGE -> TIMER ERROR: " + ex.Message);
            }
        }

        private void BridgeTick(string source)
        {
            if (State != State.Realtime)
                return;

            if (bridgeWorkInProgress)
                return;

            bridgeWorkInProgress = true;

            try
            {
                if ((DateTime.UtcNow - lastPollUtc).TotalMilliseconds >= 300)
                {
                    lastPollUtc = DateTime.UtcNow;

                    try
                    {
                        ProcessIncomingFile();
                    }
                    catch (Exception ex)
                    {
                        Print("BRIDGE -> ERROR: " + ex.Message);
                    }
                }

                // Exportar barras OHLC periódicamente
                if ((DateTime.UtcNow - lastBarExportUtc).TotalSeconds >= BAR_EXPORT_INTERVAL_SEC)
                {
                    lastBarExportUtc = DateTime.UtcNow;
                    try { ExportBars(); }
                    catch (Exception ex) { Print("BRIDGE -> BAR EXPORT ERROR: " + ex.Message); }
                }

                // Heartbeat: escribir archivo de vida por instrumento
                if ((DateTime.UtcNow - lastHeartbeatUtc).TotalSeconds >= HEARTBEAT_INTERVAL_SEC)
                {
                    lastHeartbeatUtc = DateTime.UtcNow;
                    try { WriteHeartbeat(); }
                    catch (Exception ex) { Print("BRIDGE -> HEARTBEAT ERROR: " + ex.Message); }
                }

                // HOLC: export completo por etapas (una serie por tick para no
                // congelar el hilo) + re-export de sanacion periodico.
                if (HOLC_EXPORT_ENABLED)
                {
                    try { HolcTick(); }
                    catch (Exception ex) { Print("BRIDGE -> HOLC ERROR: " + ex.Message); }
                }
            }
            finally
            {
                bridgeWorkInProgress = false;
            }
        }

        protected override void OnOrderUpdate(
            NinjaTrader.Cbi.Order order,
            double limitPrice,
            double stopPrice,
            int quantity,
            int filled,
            double averageFillPrice,
            NinjaTrader.Cbi.OrderState orderState,
            DateTime time,
            NinjaTrader.Cbi.ErrorCode error,
            string comment)
        {
            try
            {
                string orderName = order != null ? order.Name : string.Empty;
                string orderId = order != null ? order.OrderId : string.Empty;
                string orderRole = ResolveOrderRole(orderName);
                bool isExit = IsExitOrderRole(orderRole);

                bool stopIdAssignedNow = false;
                bool targetIdAssignedNow = false;

                if (string.Equals(orderRole, "Entry", StringComparison.OrdinalIgnoreCase) && string.IsNullOrWhiteSpace(currentEntryOrderId))
                    currentEntryOrderId = orderId;

                if (string.Equals(orderRole, "Stop", StringComparison.OrdinalIgnoreCase))
                {
                    if (!string.Equals(currentStopOrderId, orderId, StringComparison.OrdinalIgnoreCase))
                    {
                        currentStopOrderId = orderId;
                        stopIdAssignedNow = true;
                    }

                    if (stopPrice > 0)
                        currentActiveStopPrice = stopPrice;
                }

                if (string.Equals(orderRole, "Target", StringComparison.OrdinalIgnoreCase))
                {
                    if (!string.Equals(currentTargetOrderId, orderId, StringComparison.OrdinalIgnoreCase))
                    {
                        currentTargetOrderId = orderId;
                        targetIdAssignedNow = true;
                    }

                    if (limitPrice > 0)
                        currentActiveTargetPrice = limitPrice;
                }

                if (string.Equals(orderRole, "Exit", StringComparison.OrdinalIgnoreCase) && string.IsNullOrWhiteSpace(currentExitOrderId))
                    currentExitOrderId = orderId;

                string parentOrderId = ResolveParentOrderId(orderRole);

                Print("ORDER -> Name: " + orderName
                    + " | OrderId: " + orderId
                    + " | Role: " + orderRole
                    + " | State: " + orderState
                    + " | Qty: " + quantity
                    + " | Filled: " + filled
                    + " | AvgFill: " + averageFillPrice
                    + " | Limit: " + limitPrice
                    + " | Stop: " + stopPrice
                    + " | Error: " + error
                    + " | Comment: " + comment);

                string json =
                    "{\r\n" +
                    "  \"messageType\": \"order_update\",\r\n" +
                    "  \"clientOrderId\": " + JsonString(currentClientOrderId) + ",\r\n" +
                    "  \"strategyTag\": " + JsonString(currentStrategyTag) + ",\r\n" +
                    "  \"instrument\": " + JsonString(NormalizeInstrumentForBot(GetCurrentInstrumentName())) + ",\r\n" +
                    "  \"signalType\": " + JsonString(currentSignalType) + ",\r\n" +
                    "  \"orderRole\": " + JsonString(orderRole) + ",\r\n" +
                    "  \"orderId\": " + JsonString(orderId) + ",\r\n" +
                    "  \"parentOrderId\": " + JsonString(parentOrderId) + ",\r\n" +
                    "  \"orderName\": " + JsonString(orderName) + ",\r\n" +
                    "  \"state\": " + JsonString(orderState.ToString()) + ",\r\n" +
                    "  \"quantity\": " + quantity + ",\r\n" +
                    "  \"filled\": " + filled + ",\r\n" +
                    "  \"averageFillPrice\": " + ToJsonNumber(averageFillPrice) + ",\r\n" +
                    "  \"limitPrice\": " + ToJsonNumber(limitPrice) + ",\r\n" +
                    "  \"stopPrice\": " + ToJsonNumber(stopPrice) + ",\r\n" +
                    "  \"error\": " + JsonString(error.ToString()) + ",\r\n" +
                    "  \"comment\": " + JsonString(comment) + ",\r\n" +
                    "  \"isExit\": " + JsonBool(isExit) + ",\r\n" +
                    "  \"timeUtc\": " + JsonString(time.ToUniversalTime().ToString("o")) + "\r\n" +
                    "}";

                WriteOut("order_update", json);

                if ((stopIdAssignedNow || targetIdAssignedNow) && Position != null && Position.MarketPosition != NinjaTrader.Cbi.MarketPosition.Flat)
                    WritePositionSnapshot(Position, Position.AveragePrice, Position.Quantity, Position.MarketPosition);
            }
            catch (Exception ex)
            {
                Print("BRIDGE -> ERROR in OnOrderUpdate: " + ex.Message);
            }
        }

        protected override void OnExecutionUpdate(
            NinjaTrader.Cbi.Execution execution,
            string executionId,
            double price,
            int quantity,
            NinjaTrader.Cbi.MarketPosition marketPosition,
            string orderId,
            DateTime time)
        {
            try
            {
                string orderRole = ResolveOrderRoleByOrderId(orderId);
                bool isExit = IsExitOrderRole(orderRole);
                string parentOrderId = ResolveParentOrderId(orderRole);

                Print("EXECUTION -> ExecutionId: " + executionId
                    + " | OrderId: " + orderId
                    + " | Role: " + orderRole
                    + " | Price: " + price
                    + " | Qty: " + quantity
                    + " | MarketPosition: " + marketPosition
                    + " | Time: " + time.ToString("yyyy-MM-dd HH:mm:ss.fff"));

                string json =
                    "{\r\n" +
                    "  \"messageType\": \"execution_update\",\r\n" +
                    "  \"clientOrderId\": " + JsonString(currentClientOrderId) + ",\r\n" +
                    "  \"strategyTag\": " + JsonString(currentStrategyTag) + ",\r\n" +
                    "  \"instrument\": " + JsonString(NormalizeInstrumentForBot(GetCurrentInstrumentName())) + ",\r\n" +
                    "  \"signalType\": " + JsonString(currentSignalType) + ",\r\n" +
                    "  \"orderRole\": " + JsonString(orderRole) + ",\r\n" +
                    "  \"executionId\": " + JsonString(executionId) + ",\r\n" +
                    "  \"orderId\": " + JsonString(orderId) + ",\r\n" +
                    "  \"parentOrderId\": " + JsonString(parentOrderId) + ",\r\n" +
                    "  \"price\": " + ToJsonNumber(price) + ",\r\n" +
                    "  \"quantity\": " + quantity + ",\r\n" +
                    "  \"marketPosition\": " + JsonString(marketPosition.ToString()) + ",\r\n" +
                    "  \"isExit\": " + JsonBool(isExit) + ",\r\n" +
                    "  \"timeUtc\": " + JsonString(time.ToUniversalTime().ToString("o")) + "\r\n" +
                    "}";

                WriteOut("execution_update", json);
            }
            catch (Exception ex)
            {
                Print("BRIDGE -> ERROR in OnExecutionUpdate: " + ex.Message);
            }
        }

        protected override void OnPositionUpdate(
            NinjaTrader.Cbi.Position position,
            double averagePrice,
            int quantity,
            NinjaTrader.Cbi.MarketPosition marketPosition)
        {
            try
            {
                WritePositionSnapshot(position, averagePrice, quantity, marketPosition);

                if (marketPosition == NinjaTrader.Cbi.MarketPosition.Flat)
                    ResetCompletedPositionContext();
            }
            catch (Exception ex)
            {
                Print("BRIDGE -> ERROR in OnPositionUpdate: " + ex.Message);
            }
        }

        private void WriteHeartbeat()
        {
            string instrument = NormalizeInstrumentForBot(GetCurrentInstrumentName());
            if (string.IsNullOrWhiteSpace(instrument))
                return;

            string fileName = "heartbeat_" + instrument + ".json";
            string tempPath = Path.Combine(outPath, fileName + ".tmp");
            string finalPath = Path.Combine(outPath, fileName);

            string hasPosition = (Position != null && Position.MarketPosition != NinjaTrader.Cbi.MarketPosition.Flat) ? "true" : "false";
            bool haveTick = lastTickUtc > Core.Globals.MinDate;
            double dataAgeSec = haveTick ? (DateTime.UtcNow - lastTickUtc).TotalSeconds : 999999.0;
            string lastTickStr = haveTick ? lastTickUtc.ToString("o") : "";

            string json = "{\"instrument\":\"" + instrument
                + "\",\"utc\":\"" + DateTime.UtcNow.ToString("o")
                + "\",\"lastTickUtc\":\"" + lastTickStr
                + "\",\"dataAgeSec\":" + dataAgeSec.ToString("0.###", CultureInfo.InvariantCulture)
                + ",\"state\":\"" + State.ToString()
                + "\",\"hasPosition\":" + hasPosition
                + ",\"price\":" + (Close != null && Close.Count > 0 ? ToJsonNumber(Close[0]) : "0")
                + "}";

            try
            {
                using (var fs = new FileStream(tempPath, FileMode.Create, FileAccess.Write, FileShare.Read))
                using (var sw = new System.IO.StreamWriter(fs, Encoding.UTF8))
                    sw.Write(json);

                if (File.Exists(finalPath)) File.Delete(finalPath);
                File.Move(tempPath, finalPath);
            }
            catch
            {
                try { if (File.Exists(tempPath)) File.Delete(tempPath); } catch { }
            }
        }

        // =====================================================================
        // Exporta barras OHLC a archivos JSON para el bot externo
        // Formato: C:\NTEXECGSystem\Bridge\out\bars_MES_5m.json
        // =====================================================================
        private void ExportBars()
        {
            string instrument = NormalizeInstrumentForBot(GetCurrentInstrumentName());
            if (string.IsNullOrWhiteSpace(instrument))
                return;

            ExportBarSeries(instrument, "5m",  BarsArray[0],    BARS_5M);
            ExportBarSeries(instrument, "15m", BarsArray[IDX_15M], BARS_15M);
            ExportBarSeries(instrument, "1h",  BarsArray[IDX_1H],  BARS_1H);
            ExportBarSeries(instrument, "4h",  BarsArray[IDX_4H],  BARS_4H);
        }

        private void ExportBarSeries(string instrument, string tf, NinjaTrader.Data.Bars bars, int count)
        {
            if (bars == null || bars.Count == 0)
                return;

            int start = Math.Max(0, bars.Count - count);
            int total = bars.Count - start;

            var sb = new StringBuilder();
            sb.Append("[");

            bool first = true;
            for (int i = start; i < bars.Count; i++)
            {
                if (!first) sb.Append(",");
                first = false;

                sb.Append("\r\n  {");
                sb.Append("\"t\":\"" + bars.GetTime(i).ToString("yyyy-MM-ddTHH:mm:ss", CultureInfo.InvariantCulture) + "\",");
                sb.Append("\"o\":" + bars.GetOpen(i).ToString("0.##########", CultureInfo.InvariantCulture) + ",");
                sb.Append("\"h\":" + bars.GetHigh(i).ToString("0.##########", CultureInfo.InvariantCulture) + ",");
                sb.Append("\"l\":" + bars.GetLow(i).ToString("0.##########", CultureInfo.InvariantCulture) + ",");
                sb.Append("\"c\":" + bars.GetClose(i).ToString("0.##########", CultureInfo.InvariantCulture) + ",");
                sb.Append("\"v\":" + bars.GetVolume(i).ToString("0.##########", CultureInfo.InvariantCulture));
                sb.Append("}");
            }

            sb.Append("\r\n]");

            string fileName  = "bars_" + instrument + "_" + tf + ".json";
            string tempPath  = Path.Combine(outPath, fileName + ".tmp");
            string finalPath = Path.Combine(outPath, fileName);

            WriteAtomicJsonWithRetry(tempPath, finalPath, sb.ToString(), "bars_" + instrument + "_" + tf);
        }

        private void WriteAtomicJsonWithRetry(string tempPath, string finalPath, string content, string label)
        {
            Exception lastEx = null;

            for (int attempt = 0; attempt < 5; attempt++)
            {
                try
                {
                    using (var fs = new FileStream(tempPath, FileMode.Create, FileAccess.Write, FileShare.Read))
                    using (var sw = new System.IO.StreamWriter(fs, Encoding.UTF8))
                        sw.Write(content);

                    if (File.Exists(finalPath))
                        File.Delete(finalPath);

                    File.Move(tempPath, finalPath);
                    return;
                }
                catch (IOException ex)
                {
                    lastEx = ex;
                    System.Threading.Thread.Sleep(30);
                }
            }

            Print("BRIDGE -> WriteAtomicJsonWithRetry FAILED after 5 retries: "
                + (lastEx != null ? lastEx.Message : "unknown")
                + " | Label: " + label);

            try { if (File.Exists(tempPath)) File.Delete(tempPath); } catch { }
        }

        // =====================================================================
        // HOLC UNIFICADO — metodos
        // =====================================================================
        private void HolcTick()
        {
            if (holcFullExportPending)
            {
                holcFullExportPending = false;
                holcQueue.Clear();
                for (int i = 0; i < 4; i++) { holcReady[i] = false; holcQueue.Enqueue(i); }
                Print("BRIDGE -> HOLC full export agendado (4 series)");
            }

            if (holcQueue.Count > 0)
            {
                int idx = holcQueue.Dequeue();
                HolcExportSeriesFull(idx);
                if (holcQueue.Count == 0)
                    lastHolcFullUtc = DateTime.UtcNow;
                return;   // una serie por tick
            }

            if (HOLC_FULL_REEXPORT_HOURS > 0
                && lastHolcFullUtc != Core.Globals.MinDate
                && (DateTime.UtcNow - lastHolcFullUtc).TotalHours >= HOLC_FULL_REEXPORT_HOURS)
            {
                holcFullExportPending = true;   // sanacion periodica
            }
        }

        private void HolcExportSeriesFull(int idx)
        {
            var bars = BarsArray[idx];
            if (bars == null || bars.Count < 2)
                return;

            string instrument = Instrument.MasterInstrument.Name;   // paridad con el exporter historico
            string tf        = HOLC_TF[idx];
            string fileName  = instrument + "_" + tf + ".csv";
            string finalPath = Path.Combine(outPath, fileName);
            string tempPath  = finalPath + ".tmp";

            // En Realtime la ultima barra esta EN FORMACION -> se exporta hasta Count-2;
            // la barra en formacion llegara por append cuando cierre.
            int lastIdx = bars.Count - 2;

            using (var fs = new FileStream(tempPath, FileMode.Create, FileAccess.Write, FileShare.Read))
            using (var sw = new StreamWriter(fs, Encoding.UTF8))
            {
                sw.WriteLine("DateTime,Open,High,Low,Close,Volume");
                for (int i = 0; i <= lastIdx; i++)
                    sw.WriteLine(HolcCsvLine(bars.GetTime(i), bars.GetOpen(i), bars.GetHigh(i),
                                             bars.GetLow(i), bars.GetClose(i), bars.GetVolume(i)));
            }

            // Swap atomico SIN ventana de ausencia (File.Replace) con retry por si el
            // server esta leyendo el archivo en ese instante; Move si es la primera vez.
            bool swapped = false;
            for (int attempt = 0; attempt < 5 && !swapped; attempt++)
            {
                try
                {
                    if (File.Exists(finalPath))
                        File.Replace(tempPath, finalPath, null);
                    else
                        File.Move(tempPath, finalPath);
                    swapped = true;
                }
                catch (IOException) { System.Threading.Thread.Sleep(30); }
            }
            if (!swapped)
            {
                Print("BRIDGE -> HOLC SWAP FAILED " + fileName + " (reintenta en el proximo ciclo)");
                holcQueue.Enqueue(idx);   // re-agendar esta serie
                try { if (File.Exists(tempPath)) File.Delete(tempPath); } catch { }
                return;
            }

            lastHolcBarTime[idx] = bars.GetTime(lastIdx);
            holcReady[idx] = true;
            Print("BRIDGE -> HOLC " + fileName + " | barras: " + (lastIdx + 1)
                + " | hasta " + lastHolcBarTime[idx].ToString("yyyy-MM-dd HH:mm:ss"));
        }

        private void HolcAppendClosedBar(int bip)
        {
            if (!holcReady[bip])
                return;   // el full export de esta serie aun no corre — la barra caera en el full

            DateTime t = Times[bip][1];
            if (t <= lastHolcBarTime[bip])
                return;   // ya esta en el archivo (dedupe vs full export / reinicios)

            string instrument = Instrument.MasterInstrument.Name;
            string finalPath = Path.Combine(outPath, instrument + "_" + HOLC_TF[bip] + ".csv");
            string line = HolcCsvLine(t, Opens[bip][1], Highs[bip][1], Lows[bip][1],
                                      Closes[bip][1], Volumes[bip][1]) + "\r\n";

            for (int attempt = 0; attempt < 5; attempt++)
            {
                try { File.AppendAllText(finalPath, line, Encoding.UTF8); lastHolcBarTime[bip] = t; return; }
                catch (IOException) { System.Threading.Thread.Sleep(20); }
            }
            Print("BRIDGE -> HOLC APPEND FAILED " + instrument + "_" + HOLC_TF[bip]);
        }

        private static string HolcCsvLine(DateTime dt, double o, double h, double l, double c, double v)
        {
            return string.Format(CultureInfo.InvariantCulture,
                "{0:yyyy-MM-dd HH:mm:ss},{1},{2},{3},{4},{5}", dt, o, h, l, c, v);
        }

        private void EnsureDirectories()
        {
            Directory.CreateDirectory(inPath);
            Directory.CreateDirectory(outPath);
            Directory.CreateDirectory(processedPath);
            Directory.CreateDirectory(errorPath);
        }

        private void ProcessIncomingFile()
        {
            string[] files = Directory.GetFiles(inPath, "*.json");
            if (files.Length == 0)
                return;

            Array.Sort(files, StringComparer.OrdinalIgnoreCase);

            string file = files[0];
            string fileName = Path.GetFileName(file);
            RequestContext context = new RequestContext();

            try
            {
                string json = SafeReadAllText(file); // Corrected file reading handling.
                context = BuildRequestContext(json, fileName);

                Print("BRIDGE -> File detected: " + fileName);
                Print("BRIDGE -> messageType: " + context.MessageType);
                Print("BRIDGE -> signalType: " + context.SignalType);
                Print("BRIDGE -> clientOrderId: " + context.ClientOrderId);
                Print("BRIDGE -> strategyTag: " + context.StrategyTag);
                Print("BRIDGE -> instrument: " + context.Instrument);

                ValidateInstrumentAgainstChart(context.Instrument);

                if (string.Equals(context.MessageType, "submit_order", StringComparison.OrdinalIgnoreCase))
                {
                    ProcessSubmitOrder(context);
                }
                else if (string.Equals(context.MessageType, "modify_order", StringComparison.OrdinalIgnoreCase))
                {
                    ProcessModifyOrder(context);
                }
                else
                {
                    throw new Exception("messageType no soportado: " + context.MessageType);
                }

                Move(file, processedPath);
                Print("BRIDGE -> File moved to processed");
            }
            catch (Exception ex)
            {
                // Si el instrumento no coincide con este chart, dejar el archivo en bridge/in
                // para que el chart correcto lo procese. NO mover ni escribir error.
                if (ex.Message != null && ex.Message.IndexOf("no coincide", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    Print("BRIDGE -> Instrumento no coincide con este chart, omitiendo | " + ex.Message);
                    return;
                }

                try
                {
                    Move(file, errorPath);
                }
                catch (Exception moveEx)
                {
                    Print("BRIDGE -> ERROR moving file to error: " + moveEx.Message);
                }

                Print("BRIDGE -> File moved to error | " + ex.Message);
                WriteErrorEvent(ex.Message, fileName, context);
            }
        }

        private void ProcessSubmitOrder(RequestContext context)
        {
            if (!string.Equals(context.MessageType, "submit_order", StringComparison.OrdinalIgnoreCase))
                throw new Exception("messageType no soportado");

            if (!string.Equals(context.OrderType, "Market", StringComparison.OrdinalIgnoreCase))
                throw new Exception("Solo se soporta orderType = Market");

            if (context.Quantity <= 0)
                throw new Exception("quantity inválido");

            if (Position.MarketPosition != NinjaTrader.Cbi.MarketPosition.Flat && !context.IsExit)
            {
                // Escribir un position_update antes de rechazar para que el ExecutionManager
                // pueda recuperar el StopOrderId / TargetOrderId de la posición activa.
                WritePositionSnapshot(Position, Position.AveragePrice, Position.Quantity, Position.MarketPosition);
                throw new Exception("Ya hay una posición abierta");
            }

            ApplyRequestContext(context);

            double refPrice = Close[0];
            Print("BRIDGE -> referencePrice: " + refPrice.ToString(CultureInfo.InvariantCulture));

            if (context.IsExit)
            {
                ProcessExit(context.SignalType, context.Quantity);
                return;
            }

            ResetActiveOrderIdsForNewEntry();
            currentActiveStopPrice = context.StopLossPrice > 0 ? (double?)context.StopLossPrice : null;
            currentActiveTargetPrice = context.TakeProfitPrice > 0 ? (double?)context.TakeProfitPrice : null;

            ProcessEntry(context.SignalType, context.Quantity, refPrice, context.StopLossPrice, context.TakeProfitPrice);
        }

        private void ProcessModifyOrder(RequestContext context)
        {
            if (!string.Equals(context.MessageType, "modify_order", StringComparison.OrdinalIgnoreCase))
                throw new Exception("messageType no soportado");

            if (Position.MarketPosition == NinjaTrader.Cbi.MarketPosition.Flat)
                throw new Exception("No hay posición abierta para modificar");

            if (context.NewPrice <= 0)
                throw new Exception("newPrice inválido");

            if (!string.IsNullOrWhiteSpace(context.StrategyTag)
                && !string.IsNullOrWhiteSpace(currentStrategyTag)
                && !string.Equals(context.StrategyTag, currentStrategyTag, StringComparison.Ordinal))
            {
                throw new Exception("strategyTag no coincide con la posición activa");
            }

            if (context.IsStopModification)
            {
                ValidateStopModification(context.NewPrice);

                string appliedStopOrderId = ResolveRequestedOrActiveOrderId(context.OrderId, currentStopOrderId, "Stop");

                SetStopLoss(CalculationMode.Price, context.NewPrice);
                currentActiveStopPrice = context.NewPrice;

                Print("BRIDGE -> Stop modification applied | RequestedOrderId: " + context.OrderId
                    + " | ActiveOrderId: " + appliedStopOrderId
                    + " | NewPrice: " + context.NewPrice.ToString(CultureInfo.InvariantCulture));

                WriteModifyAck(
                    context.ClientRequestId,
                    string.IsNullOrWhiteSpace(context.StrategyTag) ? currentStrategyTag : context.StrategyTag,
                    GetCurrentInstrumentName(),
                    appliedStopOrderId,
                    "Stop",
                    context.Reason,
                    context.NewPrice);
            }
            else
            {
                ValidateTargetModification(context.NewPrice);

                string appliedTargetOrderId = ResolveRequestedOrActiveOrderId(context.OrderId, currentTargetOrderId, "Target");

                SetProfitTarget(CalculationMode.Price, context.NewPrice);
                currentActiveTargetPrice = context.NewPrice;

                Print("BRIDGE -> Target modification applied | RequestedOrderId: " + context.OrderId
                    + " | ActiveOrderId: " + appliedTargetOrderId
                    + " | NewPrice: " + context.NewPrice.ToString(CultureInfo.InvariantCulture));

                WriteModifyAck(
                    context.ClientRequestId,
                    string.IsNullOrWhiteSpace(context.StrategyTag) ? currentStrategyTag : context.StrategyTag,
                    GetCurrentInstrumentName(),
                    appliedTargetOrderId,
                    "Target",
                    context.Reason,
                    context.NewPrice);
            }
        }

        private void ProcessEntry(string signalType, int quantity, double refPrice, double stopLossPrice, double takeProfitPrice)
        {
            if (string.Equals(signalType, "Long", StringComparison.OrdinalIgnoreCase))
            {
                if (stopLossPrice > 0 && stopLossPrice >= refPrice)
                    throw new Exception("Para Long, stopLossPrice debe estar debajo del precio actual");

                if (takeProfitPrice > 0 && takeProfitPrice <= refPrice)
                    throw new Exception("Para Long, takeProfitPrice debe estar encima del precio actual");

                if (stopLossPrice > 0)
                    SetStopLoss(CalculationMode.Price, stopLossPrice);

                if (takeProfitPrice > 0)
                    SetProfitTarget(CalculationMode.Price, takeProfitPrice);

                Print("BRIDGE -> Sending EnterLong | Qty: " + quantity);
                EnterLong(quantity, "NT_LONG");
                return;
            }

            if (string.Equals(signalType, "Short", StringComparison.OrdinalIgnoreCase))
            {
                if (stopLossPrice > 0 && stopLossPrice <= refPrice)
                    throw new Exception("Para Short, stopLossPrice debe estar encima del precio actual");

                if (takeProfitPrice > 0 && takeProfitPrice >= refPrice)
                    throw new Exception("Para Short, takeProfitPrice debe estar debajo del precio actual");

                if (stopLossPrice > 0)
                    SetStopLoss(CalculationMode.Price, stopLossPrice);

                if (takeProfitPrice > 0)
                    SetProfitTarget(CalculationMode.Price, takeProfitPrice);

                Print("BRIDGE -> Sending EnterShort | Qty: " + quantity);
                EnterShort(quantity, "NT_SHORT");
                return;
            }

            throw new Exception("signalType no soportado");
        }

        private void ProcessExit(string signalType, int quantity)
        {
            if (Position.MarketPosition == NinjaTrader.Cbi.MarketPosition.Flat)
                throw new Exception("No hay posición abierta para cerrar");

            if (Position.MarketPosition == NinjaTrader.Cbi.MarketPosition.Long)
            {
                Print("BRIDGE -> Sending ExitLong | Qty: " + quantity);
                ExitLong(quantity, "NT_EXIT_LONG", "NT_LONG");
                return;
            }

            if (Position.MarketPosition == NinjaTrader.Cbi.MarketPosition.Short)
            {
                Print("BRIDGE -> Sending ExitShort | Qty: " + quantity);
                ExitShort(quantity, "NT_EXIT_SHORT", "NT_SHORT");
                return;
            }

            throw new Exception("No fue posible determinar la salida");
        }

        private void WritePositionSnapshot(
            NinjaTrader.Cbi.Position position,
            double averagePrice,
            int quantity,
            NinjaTrader.Cbi.MarketPosition marketPosition)
        {
            string instrumentName = position != null && position.Instrument != null
                ? position.Instrument.FullName
                : GetCurrentInstrumentName();

            Print("POSITION -> Instrument: " + instrumentName
                + " | Qty: " + quantity
                + " | MarketPosition: " + marketPosition
                + " | AvgPrice: " + averagePrice
                + " | EntryOrderId: " + currentEntryOrderId
                + " | StopOrderId: " + GetPositionStopOrderId()
                + " | TargetOrderId: " + GetPositionTargetOrderId()
                + " | ExitOrderId: " + currentExitOrderId
                + " | ActiveStopPrice: " + NullableToString(currentActiveStopPrice)
                + " | ActiveTargetPrice: " + NullableToString(currentActiveTargetPrice));

            string json =
                "{\r\n" +
                "  \"messageType\": \"position_update\",\r\n" +
                "  \"clientOrderId\": " + JsonString(currentClientOrderId) + ",\r\n" +
                "  \"strategyTag\": " + JsonString(currentStrategyTag) + ",\r\n" +
                "  \"instrument\": " + JsonString(NormalizeInstrumentForBot(instrumentName)) + ",\r\n" +
                "  \"signalType\": " + JsonString(currentSignalType) + ",\r\n" +
                "  \"entryOrderId\": " + JsonString(currentEntryOrderId) + ",\r\n" +
                "  \"stopOrderId\": " + JsonString(GetPositionStopOrderId()) + ",\r\n" +
                "  \"targetOrderId\": " + JsonString(GetPositionTargetOrderId()) + ",\r\n" +
                "  \"exitOrderId\": " + JsonString(currentExitOrderId) + ",\r\n" +
                "  \"activeStopPrice\": " + JsonNullableNumber(currentActiveStopPrice) + ",\r\n" +
                "  \"activeTargetPrice\": " + JsonNullableNumber(currentActiveTargetPrice) + ",\r\n" +
                "  \"averagePrice\": " + ToJsonNumber(averagePrice) + ",\r\n" +
                "  \"quantity\": " + quantity + ",\r\n" +
                "  \"marketPosition\": " + JsonString(marketPosition.ToString()) + ",\r\n" +
                "  \"timeUtc\": " + JsonString(DateTime.UtcNow.ToString("o")) + "\r\n" +
                "}";

            WriteOut("position_update", json);
        }

        private void WriteModifyAck(
            string clientRequestId,
            string strategyTag,
            string instrument,
            string orderId,
            string orderRole,
            string reason,
            double newPrice)
        {
            string json =
                "{\r\n" +
                "  \"messageType\": \"modify_ack\",\r\n" +
                "  \"clientOrderId\": " + JsonString(currentClientOrderId) + ",\r\n" +
                "  \"clientRequestId\": " + JsonString(clientRequestId) + ",\r\n" +
                "  \"strategyTag\": " + JsonString(strategyTag) + ",\r\n" +
                "  \"instrument\": " + JsonString(NormalizeInstrumentForBot(instrument)) + ",\r\n" +
                "  \"signalType\": " + JsonString(currentSignalType) + ",\r\n" +
                "  \"orderId\": " + JsonString(orderId) + ",\r\n" +
                "  \"orderRole\": " + JsonString(orderRole) + ",\r\n" +
                "  \"reason\": " + JsonString(reason) + ",\r\n" +
                "  \"newPrice\": " + ToJsonNumber(newPrice) + ",\r\n" +
                "  \"timeUtc\": " + JsonString(DateTime.UtcNow.ToString("o")) + "\r\n" +
                "}";

            WriteOut("modify_ack", json);
        }

        private void WriteErrorEvent(string message, string sourceFile, RequestContext context)
        {
            string clientOrderId = !string.IsNullOrWhiteSpace(context.ClientOrderId)
                ? context.ClientOrderId
                : currentClientOrderId;

            string strategyTag = !string.IsNullOrWhiteSpace(context.StrategyTag)
                ? context.StrategyTag
                : currentStrategyTag;

            string instrument = !string.IsNullOrWhiteSpace(context.Instrument)
                ? context.Instrument
                : currentInstrument;

            string signalType = !string.IsNullOrWhiteSpace(context.SignalType)
                ? context.SignalType
                : currentSignalType;

            string json =
                "{\r\n" +
                "  \"messageType\": \"error\",\r\n" +
                "  \"clientOrderId\": " + JsonString(clientOrderId) + ",\r\n" +
                "  \"strategyTag\": " + JsonString(strategyTag) + ",\r\n" +
                "  \"instrument\": " + JsonString(NormalizeInstrumentForBot(instrument)) + ",\r\n" +
                "  \"signalType\": " + JsonString(signalType) + ",\r\n" +
                "  \"sourceFile\": " + JsonString(sourceFile) + ",\r\n" +
                "  \"message\": " + JsonString(message) + ",\r\n" +
                "  \"timeUtc\": " + JsonString(DateTime.UtcNow.ToString("o")) + "\r\n" +
                "}";

            WriteOut("error", json);
        }

        private RequestContext BuildRequestContext(string json, string fileName)
        {
            var context = new RequestContext();
            context.SourceFile = fileName;
            context.MessageType = Parse(json, "messageType");
            context.ClientOrderId = Parse(json, "clientOrderId");
            context.ClientRequestId = Parse(json, "clientRequestId");
            context.StrategyTag = Parse(json, "strategyTag");
            context.Instrument = NormalizeInstrumentForBot(Parse(json, "instrument"));
            context.SignalType = Parse(json, "signalType");
            context.OrderType = Parse(json, "orderType");
            context.Quantity = ParseInt(json, "quantity");
            context.StopLossPrice = ParseDouble(json, "stopLossPrice");
            context.TakeProfitPrice = ParseDouble(json, "takeProfitPrice");
            context.IsExit = ParseBool(json, "isExit");
            context.OrderId = Parse(json, "orderId");
            context.Reason = Parse(json, "reason");
            context.NewPrice = ParseDouble(json, "newPrice");
            context.IsStopModification = ParseBool(json, "isStopModification");
            return context;
        }

        private void ApplyRequestContext(RequestContext context)
        {
            currentClientOrderId = context.ClientOrderId;
            currentStrategyTag = context.StrategyTag;
            currentSignalType = context.SignalType;
            currentInstrument = context.Instrument;
            currentRequestedQuantity = context.Quantity;
            currentRequestedStopLoss = context.StopLossPrice;
            currentRequestedTakeProfit = context.TakeProfitPrice;
            currentIsExit = context.IsExit;
        }

        private void ValidateInstrumentAgainstChart(string requestedInstrument)
        {
            string chartInstrument = NormalizeInstrumentForBot(GetCurrentInstrumentName());
            string requested = NormalizeInstrumentForBot(requestedInstrument);

            if (string.IsNullOrWhiteSpace(requested))
                throw new Exception("instrument vacío en request");

            if (!string.Equals(requested, chartInstrument, StringComparison.OrdinalIgnoreCase))
            {
                throw new Exception(
                    "El instrument del JSON no coincide con el chart activo | Requested: " + requested + " | Chart: " + chartInstrument);
            }
        }

        private void ValidateStopModification(double newPrice)
        {
            double refPrice = Close[0];

            if (Position.MarketPosition == NinjaTrader.Cbi.MarketPosition.Long)
            {
                if (newPrice >= refPrice)
                    throw new Exception("Para posición Long, el stop debe quedar debajo del precio actual");

                if (currentActiveStopPrice.HasValue && newPrice < currentActiveStopPrice.Value)
                    throw new Exception("Trailing stop inválido: no se permite alejar más el stop en Long");
            }
            else if (Position.MarketPosition == NinjaTrader.Cbi.MarketPosition.Short)
            {
                if (newPrice <= refPrice)
                    throw new Exception("Para posición Short, el stop debe quedar encima del precio actual");

                if (currentActiveStopPrice.HasValue && newPrice > currentActiveStopPrice.Value)
                    throw new Exception("Trailing stop inválido: no se permite alejar más el stop en Short");
            }
            else
            {
                throw new Exception("No hay posición válida para modificar stop");
            }
        }

        private void ValidateTargetModification(double newPrice)
        {
            double refPrice = Close[0];

            if (Position.MarketPosition == NinjaTrader.Cbi.MarketPosition.Long)
            {
                if (newPrice <= refPrice)
                    throw new Exception("Para posición Long, el target debe quedar encima del precio actual");
            }
            else if (Position.MarketPosition == NinjaTrader.Cbi.MarketPosition.Short)
            {
                if (newPrice >= refPrice)
                    throw new Exception("Para posición Short, el target debe quedar debajo del precio actual");
            }
            else
            {
                throw new Exception("No hay posición válida para modificar target");
            }
        }

        private void ResetActiveOrderIdsForNewEntry()
        {
            currentEntryOrderId = string.Empty;
            currentStopOrderId = string.Empty;
            currentTargetOrderId = string.Empty;
            currentExitOrderId = string.Empty;
        }

        private void ResetCompletedPositionContext()
        {
            currentEntryOrderId = string.Empty;
            currentStopOrderId = string.Empty;
            currentTargetOrderId = string.Empty;
            currentExitOrderId = string.Empty;
            currentRequestedQuantity = 0;
            currentRequestedStopLoss = 0.0;
            currentRequestedTakeProfit = 0.0;
            currentIsExit = false;
            currentActiveStopPrice = null;
            currentActiveTargetPrice = null;
        }

        private string GetPositionStopOrderId()
        {
            return string.IsNullOrWhiteSpace(currentStopOrderId)
                ? string.Empty
                : currentStopOrderId;
        }

        private string GetPositionTargetOrderId()
        {
            return string.IsNullOrWhiteSpace(currentTargetOrderId)
                ? string.Empty
                : currentTargetOrderId;
        }

        private string ResolveRequestedOrActiveOrderId(string requestedOrderId, string activeOrderId, string role)
        {
            if (string.IsNullOrWhiteSpace(activeOrderId))
                throw new Exception("No existe " + role.ToLowerInvariant() + " activo actual para modificar");

            if (string.IsNullOrWhiteSpace(requestedOrderId))
                return activeOrderId;

            if (string.Equals(requestedOrderId, activeOrderId, StringComparison.OrdinalIgnoreCase))
                return activeOrderId;

            Print("BRIDGE -> WARNING: requestedOrderId no coincide con " + role + " activo. Se aplicará usando el activo actual | Requested: "
                + requestedOrderId + " | Active: " + activeOrderId);

            return activeOrderId;
        }

        private string ResolveOrderRole(string orderName)
        {
            string normalized = (orderName ?? string.Empty).Trim().ToUpperInvariant();

            if (normalized.Contains("STOP LOSS"))
                return "Stop";
            if (normalized.Contains("PROFIT TARGET"))
                return "Target";
            if (normalized.Contains("NT_EXIT") || normalized.Contains("EXIT"))
                return "Exit";
            if (normalized == "NT_LONG" || normalized == "NT_SHORT")
                return "Entry";

            return "Unknown";
        }

        private string ResolveOrderRoleByOrderId(string orderId)
        {
            if (!string.IsNullOrWhiteSpace(currentEntryOrderId) && string.Equals(orderId, currentEntryOrderId, StringComparison.OrdinalIgnoreCase))
                return "Entry";
            if (!string.IsNullOrWhiteSpace(currentStopOrderId) && string.Equals(orderId, currentStopOrderId, StringComparison.OrdinalIgnoreCase))
                return "Stop";
            if (!string.IsNullOrWhiteSpace(currentTargetOrderId) && string.Equals(orderId, currentTargetOrderId, StringComparison.OrdinalIgnoreCase))
                return "Target";
            if (!string.IsNullOrWhiteSpace(currentExitOrderId) && string.Equals(orderId, currentExitOrderId, StringComparison.OrdinalIgnoreCase))
                return "Exit";

            return "Unknown";
        }

        private bool IsExitOrderRole(string orderRole)
        {
            return string.Equals(orderRole, "Stop", StringComparison.OrdinalIgnoreCase)
                || string.Equals(orderRole, "Target", StringComparison.OrdinalIgnoreCase)
                || string.Equals(orderRole, "Exit", StringComparison.OrdinalIgnoreCase);
        }

        private string ResolveParentOrderId(string orderRole)
        {
            if (string.Equals(orderRole, "Stop", StringComparison.OrdinalIgnoreCase)
                || string.Equals(orderRole, "Target", StringComparison.OrdinalIgnoreCase)
                || string.Equals(orderRole, "Exit", StringComparison.OrdinalIgnoreCase))
            {
                return currentEntryOrderId;
            }

            return string.Empty;
        }

        private string NormalizeInstrumentForBot(string instrument)
        {
            if (string.IsNullOrWhiteSpace(instrument))
                return string.Empty;

            // NinjaTrader FullName viene como "ROOT MM-YY" o "ROOT MONYY"
            // (p.ej. "6E SEP26", "ES 09-26"). Tomar la raiz antes del espacio
            // y quitar cualquier sufijo de contrato pegado (p.ej. "6EU2025").
            string s = instrument.Trim().ToUpperInvariant();
            int sp = s.IndexOf(' ');
            if (sp > 0) s = s.Substring(0, sp);
            s = Regex.Replace(s, "[FGHJKMNQUVXZ][0-9]{1,4}$", "");
            return s;
        }

        private string GetCurrentInstrumentName()
        {
            if (Instrument != null)
                return Instrument.FullName;

            if (!string.IsNullOrWhiteSpace(currentInstrument))
                return currentInstrument;

            return string.Empty;
        }

        private string SafeReadAllText(string file)
        {
            Exception lastException = null;

            for (int i = 0; i < 5; i++)
            {
                try
                {
                    return File.ReadAllText(file);
                }
                catch (IOException ex)
                {
                    lastException = ex;
                    System.Threading.Thread.Sleep(25);
                }
            }

            throw new Exception("No fue posible leer el archivo completo", lastException);
        }

        private string Parse(string json, string key)
        {
            Match match = Regex.Match(json, "\"" + key + "\"\\s*:\\s*\"([^\"]*)\"");
            return match.Success ? match.Groups[1].Value : string.Empty;
        }

        private int ParseInt(string json, string key)
        {
            Match match = Regex.Match(json, "\"" + key + "\"\\s*:\\s*(-?\\d+)");
            return match.Success
                ? int.Parse(match.Groups[1].Value, CultureInfo.InvariantCulture)
                : 0;
        }

        private double ParseDouble(string json, string key)
        {
            Match match = Regex.Match(json, "\"" + key + "\"\\s*:\\s*(-?\\d+(?:\\.\\d+)?)");
            return match.Success
                ? double.Parse(match.Groups[1].Value, CultureInfo.InvariantCulture)
                : 0.0;
        }

        private bool ParseBool(string json, string key)
        {
            Match match = Regex.Match(json, "\"" + key + "\"\\s*:\\s*(true|false)", RegexOptions.IgnoreCase);
            return match.Success && string.Equals(match.Groups[1].Value, "true", StringComparison.OrdinalIgnoreCase);
        }

        private string JsonString(string value)
        {
            if (value == null)
                return "null";

            string escaped = value
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\r", "\\r")
                .Replace("\n", "\\n")
                .Replace("\t", "\\t");

            return "\"" + escaped + "\"";
        }

        private string JsonBool(bool value)
        {
            return value ? "true" : "false";
        }

        private string ToJsonNumber(double value)
        {
            return value.ToString("0.##########", CultureInfo.InvariantCulture);
        }

        private string JsonNullableNumber(double? value)
        {
            return value.HasValue
                ? ToJsonNumber(value.Value)
                : "null";
        }

        private string NullableToString(double? value)
        {
            return value.HasValue
                ? value.Value.ToString("0.##########", CultureInfo.InvariantCulture)
                : "null";
        }

        private void WriteOut(string prefix, string json)
        {
            string baseName = prefix + "-" + DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fff");
            string tempPath = Path.Combine(outPath, baseName + ".tmp");
            string finalPath = Path.Combine(outPath, baseName + ".json");

            Exception lastEx = null;

            for (int attempt = 0; attempt < 5; attempt++)
            {
                try
                {
                    // Write with explicit FileShare.Read so readers don't block us
                    using (var fs = new FileStream(tempPath, FileMode.Create, FileAccess.Write, FileShare.Read))
                    using (var sw = new System.IO.StreamWriter(fs, Encoding.UTF8))
                        sw.Write(json);

                    if (File.Exists(finalPath))
                        File.Delete(finalPath);

                    File.Move(tempPath, finalPath);
                    return;
                }
                catch (IOException ex)
                {
                    lastEx = ex;
                    System.Threading.Thread.Sleep(30);
                }
            }

            // All retries failed — log and clean up orphan .tmp
            Print("BRIDGE -> WriteOut FAILED after 5 retries: " + (lastEx != null ? lastEx.Message : "unknown") + " | File: " + baseName);
            try { if (File.Exists(tempPath)) File.Delete(tempPath); } catch { }
        }

        private void Move(string file, string dest)
        {
            string target = Path.Combine(dest, Path.GetFileName(file));
            if (File.Exists(target))
                File.Delete(target);
            File.Move(file, target);
        }

        private class RequestContext
        {
            public string SourceFile = string.Empty;
            public string MessageType = string.Empty;
            public string ClientOrderId = string.Empty;
            public string ClientRequestId = string.Empty;
            public string StrategyTag = string.Empty;
            public string Instrument = string.Empty;
            public string SignalType = string.Empty;
            public string OrderType = string.Empty;
            public int Quantity = 0;
            public double StopLossPrice = 0.0;
            public double TakeProfitPrice = 0.0;
            public bool IsExit = false;
            public string OrderId = string.Empty;
            public string Reason = string.Empty;
            public double NewPrice = 0.0;
            public bool IsStopModification = false;
        }
    }
}
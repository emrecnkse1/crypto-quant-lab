# ROADMAP

Bu doküman, Local Crypto Quant Research + Execution Platform projesinin fazlarını tanımlar. Fazlar sırayla ilerler; bir sonraki faza geçmeden önce mevcut fazın hedefleri tamamlanmış olmalıdır.

- **Faz 0:** Geliştirme ortamı ve proje iskeleti
- **Faz 1:** Market data
- **Faz 2:** Historical data + storage — **Tamamlandı**
  - Backend-neutral `HistoricalCandleStore` abstraction
  - Canonical SQLite normalized historical store
  - Exact Decimal TEXT persistence
  - UTC epoch-microsecond timestamps
  - Atomic `write_batch`
  - Idempotent duplicate handling
  - `DataConflictError` conflict semantics
  - Rollback / partial write yok
  - Half-open `[start_time, end_time)` range query
  - Ascending query ordering
  - Stored-data corruption handling
  - Incompatible SQLite schema validation
  - Persistence across reopen
  - Deferred by spec: finalized/closed-only executable ingestion filtering -> historical downloader/ingestion aşamasına
  - Deferred by spec: research/backtest/strategy/risk katmanlarının backend-neutral abstraction entegrasyon testleri -> ilgili katmanlar oluşturulduğunda
- **Faz 3:** Data quality + feature foundation
- **Faz 4:** Backtest engine
- **Faz 5:** Realistic cost model
- **Faz 6:** Validation / anti-overfitting
- **Faz 7:** İlk Funding/Basis araştırması
- **Faz 8:** Risk Engine
- **Faz 9:** Paper trading
- **Faz 10:** Regime classifier
- **Faz 11:** Trend/momentum
- **Faz 12:** Mean reversion
- **Faz 13:** Strategy selector
- **Faz 14:** ML modelleri
- **Faz 15:** Local LLM / Research Agent
- **Faz 16:** RAG / Knowledge Base
- **Faz 17:** Shadow mode
- **Faz 18:** Küçük sermaye live execution
- **Faz 19:** Monitoring/dashboard
- **Faz 20:** Controlled self-improvement

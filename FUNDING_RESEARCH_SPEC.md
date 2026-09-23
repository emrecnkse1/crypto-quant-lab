# FUNDING_RESEARCH_SPEC

Bu doküman, Faz 7 — İlk Funding/Basis araştırmasının **ilk çalıştırılabilir, leakage-safe dikey dilimini** (§1–§9) ve **ikinci dikey dilimini** — USDⓈ-M perpetual kline ingestion, mekanik market provenance'ı, gerçek veri smoke run'ı (§10–§14) — tanımlar ve kapatır. Faz 7'nin tamamı bu dilimlerle TAMAMLANMIŞ SAYILMAZ.

## 1. Başlangıç Koşulu ve Kapsam

- `VALIDATION_SPEC.md` §29: Faz 7'nin minimum önkoşulu FAZ6A'dır ve karşılanmıştır. FAZ6C (CPCV, geçerli p-değeri üretimi, parameter stability, efektif-N, çok pencereli DSR, PBO yan istatistikleri) hâlâ NOT COMPLETE'tir; bu dilim onları ne tamamlar ne de engeller. FAZ6D NOT STARTED.
- Bu dilim yalnızca **funding** kullanır. **Basis kapsam dışıdır** (§6).
- Canlı emir, risk motoru, optimizer, otomatik "en iyi aday" seçimi, UI, kendini geliştirme, kullanıcı risk profili (1–10), Türkiye saatiyle 00:00 günlük öneri ve işlem yapılmama açıklamaları bu dilimde YOKTUR; roadmap'teki yerlerinde korunur.

## 2. Mevcut Durum Sınıflandırması (kaynak koddan)

```
1. Zaten mevcut ve doğrudan kullanılabilir:
   - Settled funding verisi: Binance GET /fapi/v1/fundingRate adapter'ı
     (funding/binance.py: symbol, fundingRate, fundingTime, markPrice,
     rateType), source_as_of settled-guard'lı ingestion, atomic
     events+coverage SQLite store, coverage-union kalite raporu
     (funding/quality.py).
   - Funding muhasebesi: LinearFundingModel (cost = signed_qty *
     reference_price * rate; pozitif = nakit çıkışı) ve replay'in
     "event_time <= mark_time" sweep'i (funding before fill, pre-fill
     position), store runner'ın funding_required=True kalite kapısı.
   - Transaction cost modelleri, rolling runner'lar (policy-instance
     freshness), context-aware Layer-1/2, Candidate/Trial/TrialGroup,
     Stage-1/Stage-2/annualized metrikler.
2. Mevcut ama yalnızca muhasebe/backtest maliyeti için kullanılan:
   - Funding olayları: yalnızca replay'in nakit sweep'ine girer;
     BacktestPolicy/PolicyContext funding göremez (PolicyContext
     yalnızca mum taşır, BACKTEST_SPEC ile kilitli).
3. Faz 7 için gerçekten yeni gereken (bu dilimde eklendi):
   - Karar anına kapılı settled-funding sinyal görünümü.
   - Deterministik funding-carry araştırma policy'si + no-trade kontrolü.
   - Candidate parametrelerinden policy üretimi ve rolling değerlendirme
     yardımcıları.
4. Çakışan / henüz desteklenmeyen:
   - PolicyContext'e funding eklemek kilitli BACKTEST_SPEC kontratını
     değiştirirdi -> YAPILMADI; sinyal policy factory üzerinden
     enjekte edilir (§4).
   - USDⓈ-M perpetual kline ingestion YOK: mevcut mum ingestion'ı
     yalnızca Binance Spot (/api/v3/klines). Perpetual mumları store'a
     yazılabilir (testler böyle yapar) ama gerçek perp kline adapter'ı
     eksiktir. [İkinci dilimde GİDERİLDİ: contract-trade
     /fapi/v1/klines ingestion'ı + mekanik namespace provenance'ı,
     bkz. §10.]
   - Basis: senkron spot + perpetual fiyat serisi motora girmez ve
     context tek pazarlıdır -> desteklenmez.
   - Tahmini/predicted funding oranı: yalnızca settled geçmiş saklanır.
   - Binance'in funding anı çevresindeki ~15 saniyelik pencere notu
     modellenmez (motor aynı andaki settlement'ı fill'den önce uygular).
```

## 3. Araştırma Hipotezi (yanlışlanabilir)

> Karar anında gerçekten bilinen en son **settled** funding oranı, işlem maliyetleri ve pozisyonun kendisinin settle ettiği gerçekleşen funding ödemeleri sonrasında, perpetual'ı tutmanın ileri dönem getirisi hakkında kullanılabilir bir sinyal sağlıyor mu?

```
Kaynak (resmi Binance):
  - Funding Rate History (GET /fapi/v1/fundingRate): fundingTime = oranın
    uygulandığı an; markPrice = o funding ücretiyle ilişkili mark fiyatı;
    rateType = "Regular" | "Special"; sonuçlar artan sırada.
  - "Introduction to Binance Futures Funding Rates": pozitif oranda long
    pozisyonlar short'lara, negatif oranda short'lar long'lara öder;
    yalnızca funding anında açık pozisyon öder/alır; tutar = mark fiyatı
    x pozisyon büyüklüğü x oran; ekranda gösterilen oran bir tahmindir,
    kesin oran settlement'ta hesaplanır.
Yön kuralı (projenin test ettiği hipotez, kârlılık iddiası DEĞİL):
  - rate >= short_entry_rate -> SHORT (pozitif funding'i alan taraf)
  - rate <= long_entry_rate  -> LONG  (negatif funding'i alan taraf)
  - aksi halde               -> FLAT (nötr bant = no-trade)
  Funding'i almak, fiyat hareketi ve maliyetlerden bağımsız bir kâr
  garantisi DEĞİLDİR; hipotezin sınanan kısmı tam olarak budur.
```

## 4. Temporal Availability ve Leakage (LOCKED)

```
Zaman kavramları:
  - Olay/settlement zamanı: FundingEvent.event_time (Binance fundingTime).
  - Yayınlanma/erişilebilirlik zamanı: kaynakta belgelenmez; proje
    tercihi olarak explicit publication_lag >= 0 ile modellenir:
    available_at = event_time + publication_lag.
  - Karar zamanı: PolicyContext.as_of_time (mum kapanışı =
    feature_availability_time).
  - Tahmini funding: veri yok, KULLANILMAZ; yalnızca settled oranlar.
Kural: bir settled event policy tarafından ancak
  event_time + publication_lag <= as_of_time
ise görülebilir (sınır dahil — mum availability kuralı ve replay'in
funding sweep'iyle aynı). Görünüm, bilgi kesim anı
(as_of_time - publication_lag) [coverage_start, coverage_end) dışındaysa
ValueError verir (bilinmeyen settlement tahmin edilmez).
Yasaklar (mekanik olarak uygulanır): gelecekteki settlement geçmiş
kararda kullanılmaz; ham olay dizisine public erişim yoktur; eksik oran
sıfır sayılmaz; sınırsız ileri doldurma yoktur (max_funding_age);
kapsama boşlukları kalite kapısında hata verir; aynı anda birden fazla
olay (farklı rate_type) belirsiz sinyal olarak reddedilir.
Sinyal ile muhasebe ayrıdır: policy nakde dokunmaz; gerçekleşen funding
yalnızca motor tarafından (funding_required=True) tam bir kez uygulanır.
Sınır: Python gizliliği mutlak değildir; garanti API düzeyindedir
(FundingSignalHistory ham olayları açığa çıkarmaz, değiştirilemez).
Final holdout / OOS bilgisi eşik seçiminde kullanılmaz: eşikler çağıran
tarafından açıkça verilir, bu dilimde hiçbir seçim/optimizasyon yoktur.
```

## 5. Exact API ve Veri Akışı (LOCKED)

```
# Modül: src/crypto_quant_lab/research/funding_carry.py (YENİ paket: research)

FUNDING_CARRY_STRATEGY = "funding_carry_v1"
NO_TRADE_CONTROL_STRATEGY = "no_trade_control"


class FundingSignalHistory:  # immutable, __slots__, ham olay erişimi yok
    def __init__(self, *, exchange, market_type, symbol, coverage_start,
                 coverage_end, publication_lag: timedelta,
                 events: tuple[FundingEvent, ...]) -> None: ...
    exchange / market_type / symbol / coverage_start / coverage_end /
    publication_lag  (salt-okunur property'ler)
    def latest_settled_at(self, as_of_time: datetime) -> FundingEvent | None: ...


def load_funding_signal_history(funding_store, *, exchange, market_type, symbol,
                                coverage_start, coverage_end,
                                publication_lag) -> FundingSignalHistory: ...


class FundingCarryPolicy:
    def __init__(self, history, *, short_entry_rate: Decimal,
                 long_entry_rate: Decimal, max_funding_age: timedelta) -> None: ...
    def target_position(self, context: PolicyContext) -> PositionTarget: ...


class NoTradeControlPolicy:
    def target_position(self, context: PolicyContext) -> PositionTarget: ...  # daima FLAT


def funding_carry_candidate(candidate_id, *, short_entry_rate, long_entry_rate,
                            max_funding_age, publication_lag) -> Candidate: ...
def no_trade_control_candidate(candidate_id) -> Candidate: ...
def funding_research_policy_factory(candidate, history) -> Callable[[], BacktestPolicy]: ...
def evaluate_funding_research_candidate(candle_store, funding_store, history, candidate, *,
                                        windows, timeframe, as_of_time, config,
                                        cost_model, funding_model) -> Trial: ...
```

```
Veri akışı:
  funding store --(kalite kapısı PASS + query)--> FundingSignalHistory
  Candidate.parameters --> funding_research_policy_factory (pencere başı
  taze policy) --> run_rolling_backtest_from_store(funding_required=True,
  funding_store, funding_model, cost_model) --> tuple[WindowResult] -->
  Trial --> (çağıran) Stage-1/Stage-2/annualized metrikler, TrialGroup.
Candidate parametreleri (anahtar sırası kilitli):
  long_entry_rate (Decimal), max_funding_age_us (int), publication_lag_us
  (int), short_entry_rate (Decimal), strategy ("funding_carry_v1");
  kontrol: strategy ("no_trade_control"). Factory, candidate'in
  publication_lag_us değerinin history ile eşleşmesini zorunlu kılar.
Kurallar: long_entry_rate < short_entry_rate (kesin); max_funding_age > 0;
  publication_lag >= 0; bilinen olay yoksa veya yaşı max_funding_age'i
  aşıyorsa FLAT; context mumlarının sembolü history sembolüyle aynı
  olmalıdır (market_type mumda taşınmadığından mekanik olarak
  doğrulanamaz — çağıran disiplini). Policy durumsuzdur (Type-H).
Mevcut motor dosyaları (policy.py, replay.py, store_runner.py,
  rolling.py) DEĞİŞMEZ ve research paketini import etmez.
```

## 6. Basis — Somut Eksik Bağımlılıklar

```
1. USDⓈ-M perpetual kline ingestion adapter'ı (GET /fapi/v1/klines) ve
   onun kalite/finalization sözleşmesi. [İkinci dilimde KARŞILANDI
   (§10); index-price tarafı ve basis sözleşmesi hâlâ eksik (§12).]
2. Aynı mum kapanış zamanında hem spot hem perpetual fiyatını karar
   anında gösterecek, zaman eşleşmesini birebir (yuvarlama/doldurma
   olmadan) yapan ikinci bir zaman kapılı görünüm; spot ve perpetual
   market kimliklerinin karışmasını engelleyen partition kuralları.
3. Basis tanımı (işaret ve oran formülü: (perp - spot) / spot vb.) için
   kaynak ve proje kararı.
Bunlar yokken basis ÜRETİLMEZ ve TAHMİN EDİLMEZ.
```

## 7. Sonuçların Anlamı

Üretilen değerler (WindowResult, Stage-1/Stage-2/annualized metrikler, Trial/TrialGroup) **araştırma metrikleridir**: kârlılık kanıtı, canlı işlem onayı veya risk kararı DEĞİLDİR. Gerçek veriyle çalıştırma için perpetual mumlarının store'a güvenilir biçimde yüklenmesi gerekir (§2.4) — ikinci dilimde sağlandı (§10); sınırlı gerçek veri smoke run'ı §11'dedir ve yine yalnızca araştırma smoke testidir.

## 8. Acceptance — Faz 7 İlk Dilim (23/23 IMPLEMENTATION/TEST EXERCISED)

Kanıt: `tests/test_research_funding_carry.py` (35 test, tümü PASS); ilgili funding/backtest/validation regression suite'leri (778 test) DEĞİŞMEDEN yeşil; tam suite 2320/2320 PASS (2285 önceki + 35 yeni). Beklenen nakit/equity değerleri kilitli formüllerden elle hesaplanmıştır.

1. Settled olay `event_time + publication_lag == as_of_time` anında görünür, 1 µs önce görünmez. **PASS** — `test_settled_event_is_visible_exactly_at_its_availability_boundary`, `test_zero_publication_lag_makes_the_event_visible_at_settlement`.
2. Geç yayınlanan settlement erken görünmez. **PASS** — `test_late_publication_is_not_visible_early`.
3. Bilgi kesimi kapsam dışındaysa tahmin yerine hata. **PASS** — `test_knowledge_cutoff_outside_coverage_is_an_error_not_a_guess`.
4. Karar anından sonraki funding değişiklikleri önceki kararları değiştirmez. **PASS** — `test_future_funding_changes_do_not_change_earlier_decisions`.
5. Pencere sonrasındaki funding değişikliği backtest sonucunu değiştirmez. **PASS** — `test_future_funding_after_the_window_does_not_change_the_backtest`.
6. Sırasız, yinelenen ve aynı anda birden fazla (farklı rate_type) olay reddedilir. **PASS** — `test_history_rejects_unordered_and_duplicate_events`, `test_loader_rejects_ambiguous_same_instant_rate_types`.
7. Kapsam dışı olay ve geçersiz argümanlar reddedilir. **PASS** — `test_history_rejects_events_outside_coverage_and_bad_arguments`.
8. Kapsama boşluğu yükleyicide fail-closed. **PASS** — `test_loader_fails_closed_on_a_coverage_gap`.
9. Görünüm değiştirilemez ve ham olayları açığa çıkarmaz. **PASS** — `test_history_is_immutable_and_exposes_no_raw_events`.
10. Eşik kuralları ve eşitlik sınırları (SHORT/LONG/FLAT). **PASS** — `test_threshold_rules` (7 durum).
11. Eksik oran sıfır sayılmaz. **PASS** — `test_missing_rate_is_never_treated_as_zero`.
12. Bayat oran no-trade üretir (yaş == max dahil). **PASS** — `test_stale_rate_leads_to_no_trade`.
13. Geçersiz eşikler ve yabancı sembol reddedilir. **PASS** — `test_policy_rejects_invalid_thresholds_and_foreign_symbol`.
14. No-trade kontrolü daima FLAT. **PASS** — `test_no_trade_control_is_always_flat`.
15. Candidate parametreleri konfigürasyonu eksiksiz tanımlar. **PASS** — `test_candidate_parameters_fully_describe_the_configuration`.
16. Factory candidate parametrelerinden taze policy üretir; tutarsız/bilinmeyen candidate reddedilir. **PASS** — `test_factory_builds_fresh_policies_from_candidate_parameters`, `test_factory_rejects_inconsistent_or_unknown_candidates`.
17. SHORT pozitif funding'i tam bir kez alır (1000.15). **PASS** — `test_short_receives_positive_funding_exactly_once`.
18. LONG negatif funding'i alır (1000.2). **PASS** — `test_long_receives_negative_funding`.
19. Nötr bant ve kontrol işlem üretmez, funding settle etmez (fill 0, equity 1000). **PASS** — `test_neutral_band_and_control_never_trade_or_settle_funding`.
20. İşlem maliyeti funding'e ek olarak uygulanır; zero-cost ile farkı tam komisyon (0.1). **PASS** — `test_transaction_costs_are_applied_on_top_of_funding`.
21. Bayat sinyal pozisyonu kapatır; aynı andaki settlement fill'den önce uygulanır. **PASS** — `test_stale_signal_closes_the_position_before_later_settlements`.
22. Rolling pencereler (taze policy), context-aware değerlendirme başlangıcından önce pozisyon yok, TrialGroup ve Stage-1/Stage-2/annualized metrik entegrasyonu, determinizm ve girdi değişmezliği. **PASS** — `test_rolling_windows_and_trial_group_metrics_integration`, `test_context_period_never_opens_a_position_before_evaluation_start`, `test_evaluation_is_deterministic_and_does_not_mutate_inputs`.
23. Kapsam: motor modülleri research paketini import etmez; research modülü nakde dokunmaz, özel motor yardımcılarını ve ağ kütüphanelerini kullanmaz; yeni runtime bağımlılığı yok. **PASS** — `test_engine_modules_do_not_import_the_research_layer`, `test_research_module_does_not_touch_cash_or_private_engine_helpers`; `pyproject.toml` değişmedi.

**Faz 7 ilk dilim acceptance: 23 / 23.** Bu; basis'in, perpetual kline ingestion'ının, gerçek veri üzerinde çalıştırmanın, eşik araştırmasının, çoklu-test düzeltmesinin, kârlılığın veya Faz 7'nin tamamlandığı anlamına GELMEZ.

## 9. Sıradaki Somut İş

USDⓈ-M perpetual kline ingestion (GET /fapi/v1/klines) + kalite sözleşmesi — gerçek funding ve perpetual mumlarıyla bu dilimi yerel veride çalıştırabilmenin ve basis'in ilk önkoşulu. [İkinci dilimde tamamlandı — güncel sıradaki iş §14.]

## 10. İkinci Dilim — USDⓈ-M Perpetual Kline Ingestion ve Mekanik Market Provenance'ı

**Durum:** tamamlandı (Faz 7 ikinci dikey dilim). Faz 7 bütünü TAMAMLANMADI; FAZ6C NOT COMPLETE, FAZ6D NOT STARTED — değişmedi.

### 10.1 Mum Kimliği Denetimi (kaynak koddan)

```
Soru: aynı symbol + timeframe + open_time'a sahip Binance Spot ve USDⓈ-M
perpetual mumları mevcut store'da mekanik olarak ayrılabiliyor mu?
Cevap: market_type için EVET — SQLite historical_candles birincil anahtarı
(exchange, market_type, symbol, timeframe, open_time_us); query ve
prepare_backtest_dataset market_type'ı filtreler ve doğrular; mevcut spot
ingestion market_type="spot" yazar. Ancak price_kind (contract-trade /
mark / index / continuous) ve kaynak provenance'ı anahtarda veya herhangi
bir kayıtta YOKTU: bir perpetual namespace'ine write_batch ile mark-price
mumu yazılması içerikten ayırt edilemezdi.
```

### 10.2 Seçilen Çözüm ve Migration Kararı

```
Karşılaştırılan:
  1. Mum anahtarına price_kind/dataset kimliği eklemek: historical_candles
     şemasını ve kilitli HistoricalCandleStore/HISTORICAL_DATA_SPEC
     kontratını değiştirir, mevcut dosyalar için veri migration'ı ve
     eski satırlar için bir price_kind TAHMİNİ gerektirirdi -> REDDEDİLDİ.
  2. Namespace'i immutable provenance ile bağlamak -> SEÇİLDİ, aynı SQLite
     dosyasında, ekleme niteliğinde:
     - candle_datasets(exchange, market_type, symbol, timeframe PK,
       price_kind, source): namespace başına TEK provenance.
     - candle_coverage(namespace + covered_start_us, covered_end_us PK):
       kaynağın eksiksiz paginate edildiği [start, end) aralıkları.
     - SQLiteHistoricalCandleStore.write_ingestion_batch(records, *,
       dataset, covered_start, covered_end): provenance kaydı/doğrulaması
       + mumlar + coverage TEK transaction'da (funding store'un
       write_ingestion_batch emsali).
     - query_dataset(...), query_coverage(...).
Mekanik kurallar:
  - Kayıtlı bir namespace'e eski write_batch yoluyla yazma -> StorageError.
  - Farklı price_kind/source ile yeniden kayıt -> DataConflictError.
  - Provenance'sız mum içeren bir namespace kaydedilemez ->
    DataConflictError (eski satırlar ASLA geriye dönük etiketlenmez).
Geriye uyumluluk / migration:
  - historical_candles şeması, write_batch/query davranışı (kayıtsız
    namespace'ler için) ve HistoricalCandleStore Protocol'ü DEĞİŞMEDİ.
  - Eski bir veritabanı açıldığında yalnızca iki boş tablo oluşturulur
    (historical_candles önce doğrulanır; uyumsuz şemada hiçbir şey
    oluşturulmaz). Eski spot satırlarının provenance'ı "bilinmiyor"
    (query_dataset -> None) olarak kalır; tahmin edilmez.
  - Mevcut spot ingestion değişmedi (write_batch, market_type="spot").
```

### 10.3 Dataset Kimliği

```
Canonical sabitler (src/crypto_quant_lab/storage/datasets.py):
  exchange   = "binance"
  market_type= "usdm_perpetual"   (spot: "spot")
  price_kind = "contract_trade"   (ayrıca tanımlı: "spot_trade",
               "mark_price", "index_price", "continuous_contract")
  source     = "binance:GET https://fapi.binance.com/fapi/v1/klines"
  symbol, timeframe
binance_usdm_perpetual_contract_trade_dataset(symbol, timeframe) bu kimliği
üretir. Mark/index/continuous/premium-index klines ve Binance'in hazır basis
serisi bu dilimde İNDİRİLMEZ ve contract-trade namespace'ine yazılamaz.
Funding ingestion market_type'ı çağırandan alır; araştırma girişinde
funding history'nin ("binance", "usdm_perpetual") olması zorunludur.
```

### 10.4 Endpoint, Yanıt Semantiği, Pagination ve Availability

```
Kaynaklar:
  - Resmî Binance futures connector (github.com/binance/
    binance-futures-connector-python, um_futures): base URL
    https://fapi.binance.com, GET /fapi/v1/klines, "Klines are uniquely
    identified by their open time"; connector docstring'i limit için
    "Default 500, max 1000" der. Güncel doküman arama özeti "max 1500"
    der; doküman sayfası bot korumalı olduğundan bu tur doğrudan
    okunamadı. Karar: limit <= 1500 doğrulanır, varsayılan sayfa boyutu
    her iki kaynağa göre geçerli olan 1000'dir.
  - Canlı yanıt (bu tur, şema denetimi): 12 alan — [0] open time ms int,
    [1..5] open/high/low/close/volume string, [6] close time ms int,
    [7] quote asset volume string, [8] trade sayısı int, [9]/[10] taker
    buy base/quote string, [11] ignore "0"; startTime ve endTime açılış
    zamanına göre DAHİL (00:00-01:00 isteği iki mum döndü); hata:
    HTTP 400 {"code":-1121,"msg":"Invalid symbol."}.
Parser (market_data/binance_usdm.py): tam 12 alan; fiyat/hacim JSON string
  -> Decimal (float yok); NaN/Infinity, <= 0 fiyat, negatif hacim/taker/
  quote/trade reddedilir; OHLC invariant'ları Candle ile. Candle'da
  tutulmayanlar: quote asset volume, trade sayısı, taker buy hacimleri,
  ignore (doğrulanır, sonra atılır); close time yalnızca finalization
  kontrolü için envelope'ta tutulur.
HTTP: HTTPError -> BinanceApiError (retry EDİLMEZ); URLError/timeout ->
  ConnectionError (sınırlı retry); JSON liste değilse BinanceApiError;
  sayfa limit'i aşarsa hata.
Pagination: mevcut paginate_historical_klines + transport genişletme +
  exact [requested_start, effective_end) filtresi; cursor = son açılış +
  süre; sırasız/yinelenen/cursor gerisindeki/ilerlemeyen sayfa hata verir;
  boş sayfa durdurur.
Availability: calculate_effective_end tamamlanmamış kuyruğu dışlar;
  her mum is_binance_historical_kline_finalized ile doğrulanır; close
  time tutarsızlığı hata verir. Mum feature availability kuralı
  (open_time + süre) değişmedi.
```

### 10.5 Ingestion, Coverage ve Kalite

```
ingest_binance_usdm_perpetual_klines(store, *, symbol, timeframe,
    requested_start, requested_end, as_of_time, fetch_page=None,
    max_attempts=3, page_limit=1000) -> UsdmKlineIngestionResult
  - Tüm sayfalar başarıyla alınıp doğrulanmadan hiçbir şey yazılmaz;
    ardından mumlar + provenance + coverage [requested_start,
    effective_end) tek atomik write_ingestion_batch ile yazılır.
  - Tekrar çalıştırma idempotenttir (aynı mumlar/coverage/provenance);
    değişmiş upstream değeri DataConflictError verir.
  - Sonuç: candle_count, first/last_open_time, leading_absent_count
    (ilk mumdan önceki slot'lar — listelenme öncesi olabilir, veriden
    ayırt edilemez), internal_missing_count (gerçek iç boşluklar),
    trailing_absent_count, effective_end (< requested_end ise kuyruk
    dışlandı). Boşluklar doldurulmaz.
  - Backtest kalite kapısı (prepare_backtest_dataset, dense grid) hâlâ
    her pencerede uygulanır; eksik mum FAIL eder.
```

### 10.6 Provenance-Kontrollü Araştırma Girişi

```
evaluate_usdm_perpetual_funding_research(candle_store, funding_store,
    history, candidate, *, windows, timeframe, as_of_time, config,
    cost_model, funding_model) -> Trial
  (src/crypto_quant_lab/research/usdm_perpetual.py)
Backtest başlamadan: funding history ("binance", "usdm_perpetual") olmalı;
candle store query_dataset/query_coverage sunmalı; namespace kaydı tam
olarak Binance USDⓈ-M contract-trade /fapi/v1/klines olmalı (kayıtsız/
spot/mark/index -> ValueError); her pencere candle coverage'ı içinde
olmalı; her pencerenin ilk ve son karar anındaki funding bilgi kesimi
history coverage'ı içinde olmalı. Sonra değişmemiş
evaluate_funding_research_candidate'e devreder — funding muhasebesi
motor tarafından tam bir kez uygulanır. Not: ilk dilimin
evaluate_funding_research_candidate fonksiyonu provenance kontrolü
yapmayan alt seviye primitif olarak aynen korunur; perpetual veriyle
önerilen giriş noktası bu fonksiyondur.
```

### 10.7 Dosyalar

```
Yeni: src/crypto_quant_lab/storage/datasets.py,
      src/crypto_quant_lab/market_data/binance_usdm.py,
      src/crypto_quant_lab/data_quality/usdm_ingestion.py,
      src/crypto_quant_lab/research/usdm_perpetual.py,
      tests/test_usdm_perpetual_klines.py
Değişen (ekleme niteliğinde): src/crypto_quant_lab/storage/sqlite.py
Doküman: FUNDING_RESEARCH_SPEC.md, ROADMAP.md
Yeni runtime bağımlılığı yok (stdlib urllib/json).
```

## 11. Gerçek Kamu Verisi Smoke Run (araştırma smoke testi)

```
Komut (repo kökünden; betik ve çıktılar repo DIŞINDA, commit edilmez):
  .venv/Scripts/python.exe <scratchpad>/smoke/run_smoke.py <scratchpad>/smoke/out
Sonuçlardan ÖNCE kaydedilen konfigürasyon (smoke_config.json,
SHA-256 4195d193659809b2517b821c0bb2a891ed60265f2136c8a66a285d6e1e604c74):
  BTCUSDT, 1h, binance/usdm_perpetual, contract-trade klines
  mum ingestion [2025-06-01T00:00Z, 2025-07-01T00:00Z), sayfa 1000
  funding ingestion ve history [2025-05-31T00:00Z, 2025-07-01T00:00Z),
    publication_lag 60 s
  pencereler [06-01,06-11), [06-11,06-21), [06-21,07-01) (UTC),
    backtest as_of_time 2025-07-01T00:00Z
  initial_cash 10000, position_quantity 0.01
  carry candidate "carry_s2bp_l-1bp": short_entry_rate 0.0002,
    long_entry_rate -0.0001, max_funding_age 9 h
  kontrol: no_trade_control
  maliyet: CompositeCostModel(commission 0.0005, half-spread 0.0001,
    slippage 0.0001) — taker %0.05 VIP0 varsayımı bu tur yeniden
    doğrulanmadı; sıfır maliyet yalnızca teşhis.
Çalıştırma: 2026-09-23T22:05:31Z, public endpoint'ler, API key yok.
Ham sonuçlar:
  mum ingestion: 720 mum, leading/internal/trailing boşluk 0/0/0,
    effective_end 2025-07-01T00:00Z
  funding: 93 settled olay; oran min -0.00003641, max 0.00010000;
    >= 0.0002 olan 0, <= -0.0001 olan 0
  carry (gerçekçi maliyet): 3 pencerenin hepsinde fill 0, trade 0,
    final equity 10000, total return 0, max drawdown 0; Stage-2
    Sharpe TANIMSIZ (sabit equity, stdev 0)
  no-trade kontrolü: aynı (fill 0, equity 10000)
  carry (sıfır maliyet, teşhis): aynı
Yorum sınırı: önceden belirlenen eşiklere bu sakin dönemde hiç
ulaşılmadı; hipotez bu pencerede SINANMADI. Bu smoke run yalnızca uçtan
uca veri/provenance/coverage/backtest zincirinin gerçek veriyle
çalıştığını gösterir; kârlılık, genellenebilirlik veya eşik kalitesi
hakkında HİÇBİR sonuç çıkarılamaz. Eşik/pencere/candidate sonuçtan sonra
DEĞİŞTİRİLMEDİ.
```

## 12. Basis İçin Kaynak Notu (implement EDİLMEDİ)

```
Resmî Binance seçenekleri (bu görevin talimatında belirtildiği şekliyle
kaydedilir; sözleşme sonraki görevde kaynakla kilitlenecek):
  - /futures/data/basis: indexPrice, futuresPrice, basis, basisRate
    doğrudan; yalnızca son 30 gün.
  - /fapi/v1/indexPriceKlines: daha uzun dönem için index-price tarafının
    olası kaynağı.
  - Uzun dönem basis serisi ileride zaman hizalı perpetual contract price
    ile index/spot price'tan üretilebilir; Binance'in hazır basis
    endpoint'i kısa dönem bağımsız doğrulama kaynağı olabilir.
Exact basis işareti, oran formülü ve temporal availability sözleşmesi
kilitlenmedi; bu görevde formül SEÇİLMEDİ. Basis PENDING.
```

## 13. Acceptance — Faz 7 İkinci Dilim (24/24 IMPLEMENTATION/TEST EXERCISED)

Kanıt: `tests/test_usdm_perpetual_klines.py` (47 test, tümü PASS); spot ingestion/storage, funding/replay/store/rolling/research regression'ları (461 test) DEĞİŞMEDEN yeşil; tam suite 2367/2367 PASS (2320 önceki + 47 yeni); gerçek veri smoke run'ı §11.

1. Futures base URL, `/fapi/v1/klines` ve exact parametreler. **PASS** — `test_url_uses_futures_base_path_and_exact_parameters`.
2. `limit` 1..1500 sınırı. **PASS** — `test_limit_bounds_are_enforced`.
3. Geçerli 12 alanlı satır float'sız Decimal'e ayrışır. **PASS** — `test_valid_twelve_field_row_parses_without_float`.
4. Eksik/fazla alan, geçersiz zaman/Decimal, NaN/Infinity, geçersiz fiyat/hacim/trade/taker/ignore reddedilir. **PASS** — `test_malformed_rows_are_rejected` (14 durum).
5. OHLC invariant'ları. **PASS** — `test_ohlc_invariants_are_enforced`.
6. Tek sayfa fetch, limit aşımı reddi, boş yanıt. **PASS** — `test_fetch_decodes_one_page_and_rejects_overfull_pages`, `test_empty_response_is_an_empty_page`.
7. API hata nesnesi ve HTTP hatası connection error değildir; URL hatası connection error'dır. **PASS** — `test_api_error_object_and_http_error_are_not_connection_errors`.
8. Tek sayfa, exact `[start, end)`, dahil endTime overfetch'inin filtrelenmesi. **PASS** — `test_single_page_exact_half_open_range`.
9. Çok sayfa, tam limit sayfaları, kısa son sayfa, cursor ilerlemesi. **PASS** — `test_multiple_pages_exact_limit_and_short_last_page`.
10. Sayfa sınırında duplicate reddedilir, hiçbir şey yazılmaz. **PASS** — `test_page_boundary_duplicate_is_rejected_and_nothing_is_written`.
11. Sırasız ve ilerlemeyen sayfalar reddedilir. **PASS** — `test_unordered_and_non_advancing_pages_are_rejected`.
12. Ortadaki sayfada hata: mum, coverage ve provenance commit edilmez. **PASS** — `test_mid_pagination_failure_commits_no_candles_and_no_coverage`.
13. ConnectionError sınırlı retry, tükenince fail-closed. **PASS** — `test_connection_errors_are_retried_then_fail_closed`.
14. Kapanmamış kuyruk dışlanır ve coverage'a girmez. **PASS** — `test_unfinished_tail_is_excluded_and_reported`.
15. Tutarsız close time reddedilir. **PASS** — `test_inconsistent_close_time_is_rejected`.
16. Leading/internal/trailing boşluklar raporlanır, doldurulmaz. **PASS** — `test_leading_internal_and_trailing_absences_are_reported_not_filled`.
17. Tekrar ingestion idempotent; değişmiş upstream değeri conflict. **PASS** — `test_reingestion_is_idempotent`, `test_changed_upstream_value_on_reingestion_is_a_conflict`.
18. Aynı BTCUSDT/1h/open_time spot ve perpetual kayıtları çakışmaz. **PASS** — `test_spot_and_perpetual_same_symbol_timeframe_open_time_do_not_collide`.
19. Kayıtlı namespace eski write_batch ile yazılamaz; provenance'ı bilinmeyen satırlar geriye dönük etiketlenmez; çakışan price_kind kaydı reddedilir. **PASS** — `test_registered_namespace_cannot_be_written_through_legacy_write_batch`, `test_unknown_provenance_rows_are_never_relabeled`, `test_conflicting_price_kind_registration_is_rejected`.
20. Eski veritabanı açılır, satırlar korunur, provenance "bilinmiyor" kalır, eski yazma yolu çalışır; mevcut şema testleri yeşil. **PASS** — `test_legacy_database_opens_keeps_rows_and_gains_empty_provenance_tables`, `test_dataset_record_validation`; `tests/test_storage_sqlite_schema*.py` DEĞİŞMEDEN PASS.
21. Mock HTTP → ingestion → SQLite → coverage → rolling funding policy → Trial → metrik; funding ve maliyet tam bir kez (1000.15 / 1000.10), kontrol 1000. **PASS** — `test_mocked_http_to_ingestion_to_research_trial_and_metrics`.
22. Spot ve mark-price mum dataset'leri ve spot funding history backtest başlamadan reddedilir. **PASS** — `test_spot_candle_dataset_is_rejected_before_any_backtest`, `test_mark_price_candle_dataset_is_rejected`, `test_perpetual_candles_cannot_serve_a_spot_funding_history`.
23. Eksik candle veya funding coverage backtest başlamadan reddedilir. **PASS** — `test_missing_candle_or_funding_coverage_is_rejected_before_backtest`.
24. Pencere sonrasındaki mum değişikliği önceki sonucu değiştirmez; determinizm. **PASS** — `test_later_candle_data_does_not_change_earlier_results`.

**Faz 7 ikinci dilim acceptance: 24 / 24.** Bu; basis'in, mark/index ingestion'ının, eşik araştırmasının, kârlılığın veya Faz 7'nin tamamlandığı anlamına GELMEZ.

## 14. Sıradaki Somut İş

Basis kaynak + formül + temporal availability sözleşmesinin kilitlenmesi (docs/contract): `/futures/data/basis` ve `/fapi/v1/indexPriceKlines` resmî dokümanından alan semantiği, işaret, oran formülü, index/spot seçimi ve availability kuralının kaynakla sabitlenmesi. Implementasyon, eşik araştırması ve çoklu-sembol/uzun dönem çalıştırma bu işin kapsamında DEĞİLDİR.

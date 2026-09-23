# FUNDING_RESEARCH_SPEC

Bu doküman, Faz 7 — İlk Funding/Basis araştırmasının **ilk çalıştırılabilir, leakage-safe dikey dilimini** (§1–§9) ve **ikinci dikey dilimini** — USDⓈ-M perpetual kline ingestion, mekanik market provenance'ı, gerçek veri smoke run'ı (§10–§14) — ve **üçüncü dikey dilimini** — USDⓈ-M index-price ingestion, zaman güvenli close basis, resmî basis çapraz kontrolü (§15–§16) — tanımlar ve kapatır; §17 araştırma operasyon katmanını ve güncel durum özetini içerir. Faz 7'nin tamamı bu dilimlerle TAMAMLANMIŞ SAYILMAZ.

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
   kaynak ve proje kararı. [Üçüncü dilimde: index-price ingestion ve
   yerel close basis tanımı KARŞILANDI (§15); iki bacaklı basis/carry
   için gerekenler §15.8'de, hâlâ eksik.]
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

[Üçüncü dilimde kaynakla doğrulanıp uygulandı — bkz. §15.2–§15.4.]

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

[Üçüncü dilimde tamamlandı — güncel sıradaki iş §16.] Basis kaynak + formül + temporal availability sözleşmesinin kilitlenmesi (docs/contract): `/futures/data/basis` ve `/fapi/v1/indexPriceKlines` resmî dokümanından alan semantiği, işaret, oran formülü, index/spot seçimi ve availability kuralının kaynakla sabitlenmesi. Implementasyon, eşik araştırması ve çoklu-sembol/uzun dönem çalıştırma bu işin kapsamında DEĞİLDİR.

## 15. Üçüncü Dilim — USDⓈ-M Index-Price Ingestion ve Zaman Güvenli Close Basis

**Durum:** tamamlandı (Faz 7 üçüncü dikey dilim). Bu dilim **basis veri temelidir**: basis bir araştırma feature'ı / betimsel kanıt olarak üretilir; basis işlemi, hedge, iki bacaklı carry veya PnL YOKTUR (§15.8). Faz 7 bütünü TAMAMLANMADI; FAZ6C NOT COMPLETE, FAZ6D NOT STARTED — değişmedi.

### 15.1 Kavram Ayrımı (karıştırılmaz)

```
funding signal              : settled funding oranı (/fapi/v1/fundingRate), §4
contract-trade price        : perpetual işlem fiyatı klines (/fapi/v1/klines),
                              price_kind=contract_trade, §10
index price                 : USDⓈ-M index fiyatı klines (/fapi/v1/indexPriceKlines),
                              price_kind=index_price; İŞLEM GÖREMEZ
derived close basis         : bu projenin yerel tanımı (§15.4), resmî formül DEĞİL
resmî Binance basis yanıtı  : /futures/data/basis kayıtları; yalnızca son 30 gün,
                              kısa dönem çapraz kontrol, kehanet (oracle) DEĞİL
trade edilebilir spot price : Binance Spot /api/v3/klines (market_type=spot);
                              index yerine SESSİZCE kullanılmaz
directional perpetual hipotezi : §3 funding-carry — tek bacaklı perpetual pozisyon
gerçek iki bacaklı basis/carry : spot + perpetual; bu motorda YOK (§15.8)
```

### 15.2 Resmî Kaynak Preflight (erişim: 2026-09-23 ~22:20–22:35 UTC)

```
Kaynaklar: developers.binance.com USDⓈ-M Futures REST sayfaları (JS ile
render edilir; sayfa özetleri alınabildi, bazı satır-içi yorumlar tutarsız
okundu), resmî binance-futures-connector-python (um_futures/market.py)
docstring'leri, canlı public yanıtlar (API key yok).

GET /fapi/v1/klines (contract-trade)
  parametreler: symbol, interval, startTime, endTime, limit
  limit: varsayılan 500, doküman max 1500 (connector docstring "max 1000")
  "Klines are uniquely identified by their open time."
  yanıt: 12 alan (§10.4); [0] open time = interval BAŞLANGICI.
GET /fapi/v1/indexPriceKlines (index price)
  parametreler: pair (symbol DEĞİL — canlı: symbol ile -1102 "Mandatory
  parameter 'pair'"), interval, startTime, endTime, limit
  limit: varsayılan 500, doküman max 1500 (connector "max 1000");
  sayfa boyutu 1000 kullanılır. Ağırlık [1,100)=1, [100,500)=2,
  [500,1000]=5, >1000=10. Zaman verilmezse en son mumlar döner.
  yanıt: 12 alan — [0] open time (interval başlangıcı), [1..4] OHLC
  (string), [5] ignore "0", [6] close time (= open + süre − 1 ms),
  [7] ignore, [8] int sayaç (doküman 1m örneğinde 60, canlı 1h'de 3600 —
  alt index örnek sayısı; yorumlanmaz), [9..11] ignore. Hacim YOK.
  Canlı: startTime/endTime açılış zamanına göre DAHİL.
GET /futures/data/basis (resmî basis)
  parametreler: pair, contractType {PERPETUAL, CURRENT_QUARTER,
  NEXT_QUARTER}, period {5m,15m,30m,1h,2h,4h,6h,12h,1d}, limit (varsayılan
  30, max 500), startTime, endTime
  "Only the data of the latest 30 days is available."
  yanıt alanları: indexPrice, contractType, basisRate, futuresPrice,
  annualizedBasisRate, basis, pair, timestamp
  Doküman basis / basisRate için FORMÜL VERMEZ.
  Canlı gözlemler (doğrulanmış API alanı ile yerel çıkarımı ayırarak):
    - PERPETUAL için annualizedBasisRate = "" (yıllıklandırma üretilmez,
      uydurulmaz).
    - basisRate 4 ondalıkla gösterilir.
    - basis == futuresPrice − indexPrice TAM eşit (168/168, §15.9).
    - basisRate ≈ basis / indexPrice, 4 ondalığa yuvarlanmış
      (|artık| ≤ 0.00005, 168/168) — resmî tanım değil, cebirsel gözlem.
    - timestamp T'deki kayıt, T'de BAŞLAYAN mumun AÇILIŞ fiyatlarına eşit
      (futuresPrice == contract open(T), indexPrice == index open(T):
      önce 7 probe kaydında, sonra smoke'ta 168/168). Yani kayıt T anındaki
      bir anlık görüntüdür (snapshot), periyot kapanışı değildir.
    - endTime DAHİL; en yeni kaydın yayın gecikmesi belgelenmemiştir
      (22:24 UTC'de son kayıt 21:00, kısa süre sonra 22:00 göründü) —
      bu yüzden resmî kayıtlar karar/feature girdisi olarak KULLANILMAZ.
Çelişki/sınırlar: limit (1500 vs 1000) — 1000 kullanılır ve 1500'e kadar
izin verilir; index yanıt alan yorumları sayfadan tutarlı okunamadı —
yalnızca tip/konum doğrulanır, fiyat dışı alanlar yorumlanmaz.
```

### 15.3 Provenance / Storage Kararı

```
Index-price canonical dataset:
  binance_usdm_index_price_dataset(pair, timeframe) ->
  CandleDataset(exchange="binance", market_type="usdm_perpetual",
                symbol=<pair>, timeframe, price_kind="index_price",
                source="binance:GET https://fapi.binance.com/fapi/v1/indexPriceKlines")
Namespace, pair'in contract-trade namespace'i ile AYNIDIR (index, o pair'in
USDⓈ-M futures index'idir; market_type'a sahte değer konmadı). Bir namespace
tek provenance taşıdığından (§10.2) contract-trade ve index-price aynı
fiziksel store'da BULUNAMAZ: index-price AYRI bir SQLiteHistoricalCandleStore
dosyasına yazılır. Aynı store'a yazma denemesi mekanik olarak
DataConflictError verir ve hiçbir şey yazılmaz. Şema/migration değişikliği
YOK; eski spot/perpetual verinin anlamı değişmez; provenance'ı bilinmeyen
satırlar yeniden etiketlenmez. Basis yükleyicisi iki AYRI store ister (aynı
nesne -> ValueError) ve her birinin tam olarak beklenen dataset'i
kaydettiğini ve istenen aralığı kapsadığını doğrular.
Değerlendirilip reddedilen: market_type'ı "usdm_index" gibi bir değerle
farklılaştırmak (sahte market kimliği) ve mum anahtarına price_kind eklemek
(kilitli şema migration'ı, §10.2).
```

### 15.4 Close Basis — Kesin Yerel Tanım (derived local definition)

```
Aynı pair/timeframe için open_time'ı TAM eşleşen bir contract-trade mumu C
ve bir index-price mumu I için ([open_time, close_time) aralığı):
  close_basis      = C.close − I.close          (Decimal, tam — prec 100,
                                                  Inexact trap'li özel context)
  close_basis_rate = close_basis / I.close      (34 anlamlı basamak,
                                                  ROUND_HALF_EVEN, özel context)
  işaret: > 0 premium (contract > index), < 0 discount, 0 sıfır basis
  close_time   = open_time + süre
  available_at = max(C availability, I availability), her biri
                 feature_availability_time = open_time + süre
Kurallar: yalnızca tam zaman eşleşmesi; forward-fill, nearest-neighbour,
interpolation, resampling YOK; bir tarafta mum yoksa gözlem YOK ve slot
contract_only / index_only / both_missing olarak raporlanır; I.close <= 0
veya sonlu değilse ValueError; sembol/timeframe/price_kind uyuşmazlığı
ValueError; interval başlangıcı yayın zamanı olarak kullanılmaz. Sonuçlar
process-global decimal context'inden bağımsızdır (test edildi). Yıllıklandırma
YAPILMAZ (PERPETUAL için resmî alan da boş).
Bu tanım Binance'in basisRate'i ile aynı İDDİA EDİLMEZ: resmî kayıt T
anındaki snapshot'tır; close basis [T−d, T) aralığının kapanışıdır.
```

### 15.5 Exact API

```
# market_data/binance_usdm.py (ek)
USDM_INDEX_PRICE_KLINES_PATH = "/fapi/v1/indexPriceKlines"
build_usdm_index_price_klines_url(pair, timeframe, *, start_time_ms, end_time_ms, limit) -> str
parse_binance_usdm_index_price_kline(raw, pair, timeframe) -> BinanceHistoricalKline
fetch_binance_usdm_index_price_klines(pair, timeframe, *, start_time_ms, end_time_ms,
                                      limit=1000, timeout=10.0) -> list[BinanceHistoricalKline]
fetch_usdm_json_list(url, *, limit, timeout) -> list[object]   # ortak tek-sayfa GET

# storage/datasets.py (ek)
BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE
binance_usdm_index_price_dataset(pair, timeframe) -> CandleDataset
coverage_contains(intervals, start_time, end_time) -> bool

# data_quality/usdm_ingestion.py (ek; contract-trade yolu davranışça aynı)
ingest_binance_usdm_index_price_klines(store, *, pair, timeframe, requested_start,
    requested_end, as_of_time, fetch_page=None, max_attempts=3, page_limit=1000)
    -> UsdmKlineIngestionResult

# market_data/binance_usdm_basis.py (yeni)
BinanceOfficialBasisRecord(pair, contract_type, period, timestamp, futures_price,
    index_price, basis, basis_rate, annualized_basis_rate: Decimal | None)
parse_binance_official_basis_record(raw, *, pair, contract_type, period)
build_official_basis_url(pair, contract_type, period, *, start_time_ms, end_time_ms, limit)
fetch_binance_official_basis(pair, contract_type, period, *, start_time, end_time,
    as_of_time, limit=500, timeout=10.0, fetch_rows=None) -> tuple[record, ...]
    # [start, end) yarı açık; start < as_of − 30 gün, end > as_of, hizasız aralık,
    # limit'i aşan aralık -> ValueError (sessiz kırpma yok, sayfalama yok)
check_official_basis_consistency(record) -> OfficialBasisConsistency(
    basis_residual, basis_rate_residual, is_consistent)
OFFICIAL_BASIS_RATE_DISPLAY_TOLERANCE = Decimal("0.00005")

# research/basis.py (yeni)
CloseBasisObservation(exchange, symbol, timeframe, open_time, close_time, available_at,
    contract_close, index_close, close_basis, close_basis_rate,
    contract_dataset, index_dataset)            # kurulumda yeniden doğrulanır
compute_close_basis_observation(contract, index, *, contract_dataset, index_dataset)
pair_close_basis(contract_records, index_records, *, contract_dataset, index_dataset,
    start_time, end_time) -> CloseBasisPairing(observations, contract_only_open_times,
    index_only_open_times, both_missing_open_times)     # girdi sırası önemsiz
CloseBasisHistory(*, coverage_start, coverage_end, pairing)
    .visible_at(as_of) / .latest_at(as_of)     # available_at <= as_of; as_of
    kapsam [start, end] dışındaysa ValueError; değiştirilemez; duplicate ve
    karışık provenance reddedilir
load_close_basis_history(contract_store, index_store, *, symbol, timeframe,
    start_time, end_time) -> CloseBasisHistory
compare_with_official_basis(observations, records, *, rate_tolerance)
    -> OfficialBasisComparison(rate_tolerance, comparable_count,
       official_only_timestamps, observation_only_close_times,
       max/mean_abs_basis_difference, max/mean_abs_rate_difference,
       exceeding_timestamps)
    # eşleştirme: resmî timestamp T <-> close_time == T olan gözlem
Mevcut motor/backtest modülleri DEĞİŞMEDİ; basis katmanı backtest'i import etmez.
```

### 15.6 Resmî Basis Endpoint'inin Rolü

Yalnızca sınırlı, yakın dönem çapraz kontrol: (1) resmî kaydın kendi alanlarıyla cebirsel tutarlılığı, (2) timestamp/period semantiği, (3) derived close basis ile ölçülen fark. Uzun geçmişin kalıcı kaynağı DEĞİLDİR (30 gün), karar anında girdi DEĞİLDİR (yayın gecikmesi belgelenmemiş), kusursuz referans DEĞİLDİR. Semantik fark (snapshot vs. kapanış) nedeniyle tam eşitlik beklenmez; tolerans önceden sabitlenir ve sonuçtan sonra değiştirilmez.

### 15.7 Lookahead ve Hassasiyet Önlemleri

```
- available_at = interval kapanışı; kapanıştan 1 µs önce görünmez, tam
  kapanışta görünür (sınır dahil); interval başlangıcında görünmez.
- as_of bir UTC anı olarak karşılaştırılır (+03:00 aware girdi aynı sonuç).
- Kapsam dışı as_of -> hata (bilinmeyen, yok sayılmaz).
- Bir tarafı eksik slot hiçbir as_of'ta görünmez; önceki gözlem ileri
  taşınmaz.
- Pencere sonrasındaki veri önceki görünürlüğü değiştirmez (test edildi).
- Gözlemler kurulumda yeniden hesaplanarak doğrulanır (sahte gözlem reddi).
- Decimal: yalnızca özel Context nesneleri; ortam context'i prec=3 /
  ROUND_DOWN iken sonuç aynı (test edildi).
```

### 15.8 Çok Bacaklı Kapasite Denetimi ve Kapsam Sınırı

```
Kod kanıtı: backtest motoru TEK enstrüman, TEK pozisyonludur —
  backtest/replay.py: tüm mumların aynı sembolü paylaşması zorunlu
  ("all candles must share the same symbol"); funding olayları da aynı
  sembole bağlı.
  backtest/accounting.py: AccountState tek position_quantity + tek
  average_entry_price; equity = cash + position_quantity * mark_price.
  backtest/models.py: PositionTarget LONG/SHORT/FLAT tek hedef.
Sonuç: mevcut motor iki bacaklı basis/carry'yi TEMSİL EDEMEZ. Bu dilimde
basis-eşik trading policy'si, hedge, "basis arbitrage", "market-neutral
carry" veya PnL ÜRETİLMEDİ; index price trade edilebilir bacak olarak
modellenmedi. Basis yalnızca araştırma feature'ı / betimsel kanıttır.
Gerçek basis/carry araştırması için gerekenler (hiçbiri mevcut değil):
  - trade edilebilir spot bacağı (spot fiyat serisi + spot execution)
  - perpetual bacağı
  - iki ayrı fill akışı
  - bacak başına komisyon / spread / slippage
  - funding nakit akışı (yalnız perpetual bacağında)
  - borrow/financing ve erişilebilirlik varsayımları (spot short için)
  - hedge oranı
  - senkron execution varsayımı
  - liquidation / margin modeli
  - partial-fill ve legging riski
  - portföy seviyesinde equity / drawdown
```

### 15.9 Gerçek Kamu Verisi Smoke Validation (betimsel)

```
Komut (repo kökünden; betik/çıktılar repo DIŞINDA, commit edilmez):
  .venv/Scripts/python.exe <scratchpad>/smoke3/run_basis_smoke.py <scratchpad>/smoke3/out
Önceden kaydedilen konfigürasyon (smoke_config.json, 2026-09-23T22:33:00Z):
  config SHA-256 66c9643489ee8984753f6ee81ffd3735637e89507b54ad660a87a7e69e3d5d4d
  script SHA-256 6a2fb9e26547aabd132e038b8b7df5a3a418304023454fddc17ac856c442b620
  BTCUSDT, 1h (timeframe = period), PERPETUAL
  pencere kuralı: end = çalıştırma saatinin UTC gece yarısı, start = end − 7 gün
    -> [2026-09-16T00:00Z, 2026-09-23T00:00Z) (kural farklı sonuç verirse abort)
  sayfa limitleri: contract 1000, index 1000, resmî basis 500
  saat: script başında tek wall-clock UTC okuması (as_of)
  formül: §15.4; ayrı contract.db / index.db store'ları
  resmî cebirsel kontrol: basis artığı tam 0; |basisRate − basis/indexPrice| <= 0.00005
  karşılaştırma: T <-> close_time T; rate toleransı 0.0001 (1 bp);
    beklenen yapısal eşleşmeme: pencere başındaki resmî kayıt ve pencere
    sonunda kapanan gözlem — birer tane
  betimsel: resmî fiyatların T'deki mum AÇILIŞLARINA tam eşitliği sayılır
Not: 7 kayıtlık bir canlı probe (preflight) konfigürasyondan önce yapıldı ve
snapshot/açılış eşitliği orada ilk kez gözlendi; tolerans ve pencere smoke
sonucundan ÖNCE sabitlendi ve sonra değiştirilmedi.
Çalıştırma: 2026-09-23T22:33:34Z, public endpoint'ler, API key yok.
Ham sonuçlar:
  contract mumları 168, index mumları 168; her ikisinde leading/internal/
    trailing boşluk 0/0/0; effective_end 2026-09-23T00:00Z
  eşleşen gözlem 168; contract_only 0, index_only 0, both_missing 0
  close_basis min −64.48630435, max 21.47739130,
    ortalama −32.79697670803571428571428571428571
  close_basis_rate min −0.0007556204866843568417337703913317498,
    max 0.0002770421126444617447671976136631324,
    ortalama −0.0004063050729842611646614204780665633
  premium 3, discount 165, sıfır 0
  resmî kayıt 168 (2026-09-16T00:00Z … 2026-09-22T23:00Z);
    annualizedBasisRate dolu kayıt 0
  cebirsel tutarlılık 168/168; sıfırdan farklı basis artığı 0;
    en büyük |rate artığı| 0.0000498570111333745819323176775791208 (<= 0.00005)
  karşılaştırma: karşılaştırılabilir 167; resmî-only 1 (2026-09-16T00:00Z),
    gözlem-only 1 (close 2026-09-23T00:00Z) — önceden beklenen yapısal fark
    max |basis farkı| 19.27065217, ortalama 1.118701444850299401197604790419162
    max |rate farkı| 0.0002387788371603713897608277703212235,
    ortalama 0.00001384156357173724746267484621979918
    tolerans 0.0001 AŞIMI: 4 zaman damgası — 2026-09-16T11:00Z,
    2026-09-17T16:00Z, 2026-09-21T13:00Z, 2026-09-22T01:00Z
  snapshot/açılış eşitliği: 168/168
Uyuşmazlık açıklaması (sonuç sonrası, yalnız teşhis; tolerans DEĞİŞMEDİ):
  4 aşımın hepsinde fark index tarafından gelir — index mumunun kapanışı ile
  bir sonraki index mumunun açılışı ayrışır (ör. 11:00Z: index close(10:00)
  75942.92434783 vs open(11:00) 75960.95804348; contract close/open farkı
  ≤ 0.1). Resmî kayıt T açılış snapshot'ı olduğundan, close basis ile resmî
  basis arasındaki fark bu "index kapanış→açılış sıçraması"nı içerir.
  Bu bir semantik farktır; close basis tanımı veya tolerans buna göre
  yumuşatılmadı. 1 bp önceden seçilmiş toleransla 167 noktanın 4'ü aşar.
Yorum sınırı: veri zinciri (iki ayrı provenance'lı ingestion, tam eşleşme,
zaman kapısı, resmî kayıt denetimi) gerçek veride çalıştı. Kârlılık,
arbitraj fırsatı, işlem sinyali veya hipotez kanıtı İDDİA EDİLMEZ; PnL/Sharpe
üretilmedi.
```

### 15.10 Acceptance — Faz 7 Üçüncü Dilim (25/25 IMPLEMENTATION/TEST EXERCISED)

Kanıt: `tests/test_usdm_index_basis.py` (72 test, tümü PASS); `tests/test_usdm_perpetual_klines.py` (47) ve diğer storage/market-data/research testleri DEĞİŞMEDEN yeşil; tam suite 2439/2439 PASS (2367 önceki + 72 yeni); gerçek veri smoke §15.9.

1. Index URL `pair` parametresi ve exact path. **PASS** — `test_index_url_uses_pair_parameter_and_exact_path`.
2. limit 1..1500, boş pair, ters aralık reddi. **PASS** — `test_index_limit_and_range_bounds_are_enforced`, `test_index_invalid_pair_and_range_are_rejected`.
3. Geçerli index satırı float'sız, tam Decimal; hacim 0 sözleşmesi. **PASS** — `test_valid_index_row_parses_exact_decimals_and_zero_volume`.
4. Bozuk alan sayısı/tip/NaN/<=0 fiyat/sayaç/ignore/zaman reddi; OHLC. **PASS** — `test_malformed_index_rows_are_rejected` (14 durum), `test_index_ohlc_invariants_are_enforced`.
5. Tek sayfa fetch, limit aşımı ve API hata nesnesi fail-closed. **PASS** — `test_fetch_index_page_decodes_and_fails_closed`.
6. Sayfalama, yarı açık aralık, kapanmamış kuyruk dışlama, exact coverage ve provenance. **PASS** — `test_index_ingestion_paginates_half_open_and_excludes_unclosed_tail`.
7. Canonical index dataset kimliği. **PASS** — `test_index_dataset_identity_is_canonical`.
8. Duplicate/sırasız satır ve ortada API hatası: mum, provenance, coverage yazılmaz. **PASS** — `test_index_duplicate_or_unordered_rows_write_nothing`, `test_index_mid_pagination_api_error_rolls_back_everything`.
9. Contract-trade ve index-price aynı store'u paylaşamaz. **PASS** — `test_contract_and_index_can_never_share_one_store`.
10. İdempotent tekrar; değişmiş değer conflict; eski satırlar yeniden etiketlenmez. **PASS** — `test_index_reingestion_is_idempotent_and_changed_value_conflicts`, `test_legacy_rows_in_index_namespace_are_never_relabeled`.
11. Coverage birleşimi exact. **PASS** — `test_coverage_contains_is_exact`.
12. Premium/discount/sıfır, exact Decimal formül. **PASS** — `test_close_basis_premium_discount_zero_exact`.
13. 34 basamak, bağımsız Fraction ile doğrulanmış değer, ortam context'inden bağımsızlık. **PASS** — `test_close_basis_rate_precision_is_fixed_and_context_independent`.
14. Sıfır/negatif index reddi. **PASS** — `test_zero_or_negative_index_close_is_rejected`.
15. Sembol/timeframe/price_kind uyuşmazlığı ve hizasız interval reddi. **PASS** — `test_mismatched_symbol_timeframe_and_price_kind_are_rejected`, `test_misaligned_intervals_are_rejected`.
16. Sahte gözlem reddi. **PASS** — `test_forged_observation_is_rejected`.
17. Boşluklar raporlanır, doldurulmaz; girdi sırası sonucu değiştirmez; duplicate/aralık dışı reddi. **PASS** — `test_pairing_reports_gaps_and_never_fills`, `test_pairing_rejects_duplicates_out_of_range_and_misaligned_range`.
18. Kapanıştan önce görünmez, tam kapanışta görünür, interval başlangıcı yayın zamanı değil, gelecek dışlanır. **PASS** — `test_observation_is_invisible_before_close_and_visible_exactly_at_it`.
19. UTC anı karşılaştırması; kapsam dışı as_of hata. **PASS** — `test_as_of_is_compared_as_a_utc_instant`, `test_as_of_outside_coverage_is_an_error_not_an_empty_answer`.
20. Eksik mum hiç görünmez, ileri taşınmaz; sonraki veri önceki görünürlüğü değiştirmez. **PASS** — `test_missing_candle_on_either_side_never_becomes_visible`, `test_later_data_does_not_change_earlier_visibility`.
21. History değiştirilemez; duplicate ve karışık provenance reddi; yükleyici ayrı, kayıtlı, kapsanmış store ister. **PASS** — `test_history_is_immutable_and_rejects_duplicates_and_mixed_provenance`, `test_loader_enforces_separate_registered_and_covered_stores`.
22. Basis katmanı trading/muhasebe'ye bağımlı değil. **PASS** — `test_basis_layer_has_no_trading_or_accounting_dependency`.
23. Resmî kayıt birebir ayrıştırma; bozuk kayıt reddi; URL/parametre sözleşmesi. **PASS** — `test_official_record_parses_verbatim`, `test_malformed_official_records_are_rejected`, `test_official_url_and_parameter_contract`.
24. Resmî fetch yarı açık/sıralı; 30 gün penceresi ve saat sözleşmesi; API hatası fail-closed. **PASS** — `test_official_fetch_is_half_open_and_ordered`, `test_official_fetch_enforces_the_30_day_window_and_clock`, `test_official_fetch_api_error_is_fail_closed`.
25. Cebirsel tutarlılık (dahil sınır) ve close_time↔snapshot eşleştirmeli karşılaştırma metrikleri. **PASS** — `test_official_record_algebraic_consistency`, `test_official_rate_tolerance_boundary_is_inclusive`, `test_comparison_matches_close_time_to_snapshot_timestamp_and_measures_differences`, `test_comparison_rejects_mismatches_and_bad_tolerance`.

**Faz 7 üçüncü dilim acceptance: 25 / 25.** Bu; iki bacaklı basis/carry araştırmasının, spot bacağının, eşik araştırmasının, kârlılığın veya Faz 7'nin tamamlandığı anlamına GELMEZ.

## 16. Sıradaki Somut İş

[2026-09-24 itibarıyla hâlâ geçerli; §17 operasyon paketi bunu değiştirmedi.]

Çok bacaklı (trade edilebilir Binance Spot bacağı + USDⓈ-M perpetual bacağı) muhasebe/execution sözleşmesinin yazılması: iki fill akışı, bacak başına maliyet, yalnız perpetual bacağında funding, hedge oranı, senkron execution/legging varsayımı, margin/liquidation sınırı ve portföy equity'si — mevcut tek bacaklı motor kontratını bozmadan. Bu yapılmadan gerçek basis/carry hipotezi sınanamaz.

## 17. Araştırma Operasyon Katmanı (gece paketi, 2026-09-24)

**Durum:** tamamlandı. Yeni araştırma yeteneği, strateji veya ekonomik varsayım EKLEMEZ; mevcut production API'lerini tekrar çalıştırılabilir, raporlanabilir ve denetlenebilir hâle getirir. Kullanım: `docs/RESEARCH_RUNBOOK.md`.

### 17.1 Komutlar

```
python -m crypto_quant_lab.research doctor            --config C [--output O]
python -m crypto_quant_lab.research inspect           --config C --output O
python -m crypto_quant_lab.research basis-report      --config C --output O
python -m crypto_quant_lab.research funding-research  --config C --output O
python -m crypto_quant_lab.research offline-smoke     --output O
python -m crypto_quant_lab.research public-smoke      --allow-network [--symbol BTCUSDT|ETHUSDT]... --output O
```
Varsayılan offline; ağ yalnız `public-smoke --allow-network` ile. Config JSON
v1 (runbook §7): float yasak (ondalıklar string), açık UTC offset'i, ızgaraya
hizalı aralık, `as_of >= end`, alan adını söyleyen erken hata.

### 17.2 Salt okunur store erişimi

Var olan bir store, production store sınıfıyla açılmadan önce read-only SQLite
bağlantısıyla denetlenir: dosya yoksa oluşturulmaz; production store'un
oluşturacağı tablolardan biri eksikse (eski/yabancı dosya) açılmaz — böylece
araçlar kullanıcı DB'sine tablo ekleyemez. Tüm tablolar varken store açmak
yalnız no-op `CREATE TABLE IF NOT EXISTS` çalıştırır (dosya baytları
değişmez; testle doğrulandı).

### 17.3 Rapor/manifest sözleşmesi (`research/report.py`)

```
schema_version   "crypto-quant-lab/research-report/v1"
run_kind, status ("succeeded" | "failed": hata ya da başarısız kontrol varsa failed)
deterministic    config (store yolları yalnız dosya adı), config_sha256, inputs,
                 checks[{name, status passed|failed|skipped, detail}], results,
                 errors, limitations, does_not_prove
deterministic_sha256   SHA-256(canonical JSON(deterministic))
run_metadata     created_at, git_revision, git_tracked_changes, package/python
                 sürümü, decimal context — HASH'E GİRMEZ
```
Serileştirme: Decimal -> string, datetime -> UTC "+00:00", float/NaN/naive
reddedilir, anahtarlar sıralı, kompakt, ASCII. Girdi parmak izi MANTIKSALDIR:
store API'lerinin döndürdüğü satırlar + provenance + coverage üzerinden
SHA-256; SQLite dosya baytları hash'lenmez (sayfa düzeni/WAL/journal içerikten
bağımsız değişir ve açık dosya tutarlı snapshot değildir). Çıktı: hedefin
yanında staging dizini, tamamlanınca `rename`; var olan hedef asla ezilmez;
başarısız çalıştırma da `failed` durumlu bir paket üretir. Hata mesajlarında
kullanıcı ev dizini `~` ile maskelenir.

### 17.4 Offline fixture (`research/offline_fixture.py`)

`FIXTUREUSDT`, 1h, 24 mum; üç store production ingestion yoluyla kurulur.
Senaryolar ve elle türetilmiş beklentiler modül docstring'indedir: pozitif /
negatif / sıfır close basis, hour 9'da index eksikliği (doldurulmaz),
önceden sabitlenmiş eşiğin tetiklendiği pencere (2 fill, final equity
999.805 = 1000 − 2·0.1 komisyon + 0.005 funding), eşiğin tetiklenmediği pencere
(0 fill) ve no-trade kontrolü. Eşik, gerçek smoke'taki candidate ile aynıdır
(short ≥ 0.0002, long ≤ −0.0001, 9 saat); sonuçlara göre değiştirilmedi.

### 17.5 "Neden işlem yok?" teşhisi (`research/diagnostics.py`)

Policy artık tek saf kuralı `decide_funding_carry` çağırır (davranış aynı:
mevcut policy testleri değişmeden geçer ve kural/policy eşdeğerliği ayrıca
test edilir). Teşhis, değerlendirilmiş bir Trial'ın karar anlarını bu kuralla
salt okunur yeniden oynatır ve ayrı sayımlar verir: değerlendirilen karar,
sinyal görülebilen, taze sinyal, eşiği karşılayan, hedef değişimi,
uygulanabilir hedef değişimi, son mumda uygulanamayan karar, motorun kendi
fill/trade sayısı. Nedenler yalnız kodda gerçekten ayrışanlardır
(`no_settled_signal`, `stale_signal`, `neutral_band`, `short_threshold_met`,
`long_threshold_met`, kontrol kolu için `control_always_flat`). Risk filtresi
yoktur, uydurulmaz. Bu, Faz 8 Risk Engine'i veya kullanıcıya dönük
"işlem açılmama açıklaması" özelliğini TAMAMLAMAZ.

### 17.6 Regression denetimi

`tests/test_faz7_regression_audit.py` (20 test): naive zaman reddi (yan etki
öncesi), eşdeğer timezone anları, sınırlı index retry, başarısız ikinci
ingestion'ın önceki coverage'ı koruması, bitişik/sırasız ingestion'ların tek
kapsama oluşturması, aynı fiziksel DB'nin başka yol/bağlantıyla verilmesi,
coverage birleşimi uç durumları, sNaN/−Infinity, ortam Decimal
hassasiyet/trap'lerinin basis katmanına sızmaması, girdi mutasyonu yokluğu.
Production bug bulunmadı. Bulgu (düzeltilmedi, kilitli sözleşme): motorun
muhasebe/maliyet aritmetiği process-global Decimal context'ini kullanır
(COST_MODEL_SPEC.md); CLI context'i değiştirmez ve raporda kaydeder.

### 17.7 Public smoke (opt-in) sonucu — 2026-09-23T22:54Z

Komut: `public-smoke --allow-network --symbol BTCUSDT --symbol ETHUSDT`.
Pencere kuralla [2026-09-16T00:00Z, 2026-09-23T00:00Z) — §15.9 ile AYNI
pencere; yani BTCUSDT verisi daha önce görülmüş veridir, "görülmemiş veri
doğrulaması" değildir. Tolerans 0.0001 kod sabitidir (§15.9'dan devralındı).
```
BTCUSDT: 168/168 contract ve index mumu, 168 eşleşme, boşluk 0; resmî 168
  kayıt 168/168 cebirsel tutarlı; 167 karşılaştırma, 4 tolerans aşımı —
  §15.9 ile birebir aynı istatistikler (bağımsız CLI yolu aynı sonucu üretti)
ETHUSDT: 168/168, 168 eşleşme, boşluk 0; premium 5, discount 163, sıfır 0;
  close_basis min −2.45279070, max 0.51069767; resmî 168/168 tutarlı;
  167 karşılaştırma, max |rate farkı| 0.000261348, ortalama 0.0000217834;
  7 tolerans aşımı (09-16 11:00, 15:00, 19:00, 23:00; 09-17 15:00, 16:00, 17:00)
Rapor durumu: failed (within_rate_tolerance kontrolleri) — hata yok.
```
Yorum sınırı: snapshot (resmî kayıt) ile kapanış (close basis) semantik farkı
nedeniyle aşımlar beklenebilir (§15.9 teşhisi yalnız BTCUSDT'nin 4 noktası
içindi; ETHUSDT aşımları bu gece ayrıca teşhis EDİLMEDİ). Kârlılık, arbitraj
veya sinyal iddiası yoktur.

### 17.8 Güncel Durum Özeti (2026-09-24; tarihsel dilim sayıları kendi bölümlerinde kalır)

```
Faz 6: FAZ6A, FAZ6B tamamlandı; FAZ6C TAMAMLANMADI (CPCV, geçerli p-değeri
       üretimi / aile kapsamı / seçim politikası, parameter stability,
       efektif-N, çok pencereli DSR, PBO yan istatistikleri açık);
       FAZ6D BAŞLAMADI.
Faz 7: funding temeli, contract-trade ingestion, index-price/close-basis
       temeli ve araştırma operasyon katmanı tamamlandı; Faz 7 bütünü
       TAMAMLANMADI. Motor tek bacaklı; close basis araştırma feature'ı;
       index trade edilebilir bacak değil; gerçek basis/carry hedge'i yok.
Sıradaki somut iş: §16 (çok bacaklı muhasebe/execution sözleşmesi) — değişmedi.
```
Bölüm 8, 13 ve 15.10'daki test sayıları (2320, 2367, 2439) o dilimlerin
tarihindeki anlık görüntülerdir; güncel sayı `docs/NIGHT_CHECKPOINT.md`'dedir.

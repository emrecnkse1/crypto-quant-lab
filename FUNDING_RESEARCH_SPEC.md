# FUNDING_RESEARCH_SPEC

Bu doküman, Faz 7 — İlk Funding/Basis araştırmasının **ilk çalıştırılabilir, leakage-safe dikey dilimini** (§1–§9) ve **ikinci dikey dilimini** — USDⓈ-M perpetual kline ingestion, mekanik market provenance'ı, gerçek veri smoke run'ı (§10–§14) — ve **üçüncü dikey dilimini** — USDⓈ-M index-price ingestion, zaman güvenli close basis, resmî basis çapraz kontrolü (§15–§16) — tanımlar ve kapatır; §17 araştırma operasyon katmanını, §18 onun güvenilirlik düzeltmelerini ve ETH teşhisini, §19 çok bacaklı muhasebe/execution TASLAĞINI (DRAFT) içerir. Faz 7'nin tamamı bu dilimlerle TAMAMLANMIŞ SAYILMAZ.

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

[2026-09-24 itibarıyla hâlâ geçerli; §17 operasyon paketi bunu değiştirmedi. Kodla eşleştirilmiş tasarım taslağı §19'dadır (DRAFT, kilitli değil; implementasyon yok).]

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

[2026-09-24 sonrası: §18 güvenilirlik düzeltmeleri tamamlandı; §16 için taslak §19'da, karar bekliyor.]
Bölüm 8, 13 ve 15.10'daki test sayıları (2320, 2367, 2439) o dilimlerin
tarihindeki anlık görüntülerdir; güncel sayı `docs/NIGHT_CHECKPOINT.md`'dedir.

## 18. Araştırma Operasyon Katmanı Güvenilirlik Düzeltmeleri (2026-09-24)

**Durum:** tamamlandı (commit `644f66b`). §17'deki tarihsel sonuçlar değiştirilmedi; bu bölüm onların üzerine gelen düzeltmeleri kaydeder.

### 18.1 Açık Decimal sözleşmesi

```
Deney (kanıt): offline fixture'da funding-research bölümü ortam context'i
  default iken final equity 999.80500; ortam prec=5 / prec=4 ROUND_DOWN /
  prec=3 ROUND_UP iken motor "total_pnl consistency invariant violated"
  hatası verdi; trap Inexact veya Rounded (prec 28) sonucu değiştirmedi.
  İlk yuvarlama: backtest/accounting.py apply_cash_cost (replay'in funding
  sweep'inde nakit - funding maliyeti; 999.805 altı basamak ister).
Karar: motor ve COST_MODEL_SPEC §20 ("konfigüre edilmiş Decimal context")
  DEĞİŞMEDİ. Context araştırma orchestration sınırında bir RUN GİRDİSİ oldu:
  research/decimal_policy.py — config'teki opsiyonel `decimal_context`
  {prec, rounding, Emin, Emax, capitals, clamp, traps} doğrulanır, her
  hesaplama bundan kurulan TAZE Context (flags boş) ile localcontext içinde
  çalışır; çağıranın context'i değişmez. Flags konfigürasyon değildir.
Varsayılan (config'te alan yoksa): Python'un belgelenmiş DefaultContext'i,
  alan alan yazılmış — prec 28, ROUND_HALF_EVEN, Emin -999999, Emax 999999,
  capitals 1, clamp 0, traps {InvalidOperation, DivisionByZero, Overflow}.
  Motorun tüm test suite'i bu context'te koşar; prec/rounding/E-sınırları
  VALIDATION_SPEC §15.7 metrik context'iyle aynıdır. Ortam durumundan
  OKUNMAZ.
Sürüm uyumluluğu: config_version 1 korunur; `decimal_context` opsiyoneldir.
  Alan içermeyen eski config'ler varsayılanla, yani önceki CLI sürecinin
  fiilen kullandığı context ile çalışır. Raporun config'ine her zaman
  ÇÖZÜLMÜŞ context yazılır.
Basis ve metrik modüllerinin özel context'leri aynen korunur.
Parmak izleri: deterministic.run_input_sha256 = SHA-256(etkin config +
  girdilerin mantıksal parmak izleri) — GİRDİ kimliği; deterministic_sha256
  = çıktı payload'unun hash'i. Çıktı hash eşitliği tek başına girdi
  eşitliğini kanıtlamaz. Test: düşmanca ortam context'inde (prec 5,
  ROUND_DOWN, Inexact+Rounded trap) offline-smoke aynı sonucu ve aynı iki
  hash'i verir; config prec 50 aynı ekonomik sonucu farklı girdi hash'iyle,
  config prec 5 motor hatasıyla failed rapor verir.
```

### 18.2 Gerçek salt okunur store erişimi

```
Önceki durum (koddan doğrulandı): CLI read-only bağlantıyla tablo varlığını
  denetleyip ardından NORMAL (yazılabilir) production store'u açıyordu;
  garanti "CREATE TABLE IF NOT EXISTS no-op" ve bayt eşitliğine dayanıyordu.
Yeni (additive; normal constructor ve ingestion davranışı değişmedi):
  storage/sqlite_readonly.py — connect_read_only: `file:` URI, mode=ro,
    yol yüzde-kodlanır (boşluk, Unicode, `#`, `%`, `&`, `=` harfiyen),
    PRAGMA query_only=ON, isolation_level=None; olmayan dosya
    FileNotFoundError (yaratılmaz); immutable=1 KULLANILMAZ (DB canlı olabilir).
  SQLiteHistoricalCandleStore.open_read_only / SQLiteHistoricalFundingStore
    .open_read_only: şema başlatma yok; eksik tablo -> StorageError;
    uyumsuz şema -> DataCorruptionError (constructor ile aynı doğrulama);
    her yazma "readonly" StorageError verir.
  read_snapshot(): store başına tek okuma işlemi (BEGIN ... ROLLBACK). WAL'da
    snapshot izolasyonu (eşzamanlı commit görünmez), rollback-journal'da
    SHARED kilit yazanı bekletir; kilit 5 sn'de alınamazsa StorageError —
    tutarsız okuma yapılmaz. Dosyalar arası atomik snapshot YOKTUR; her
    store'un sorguları ve parmak izi aynı commit durumundan gelir.
  CLI (inspect, basis-report, funding-research, doctor) yalnız bu yolu
    kullanır; aynı fiziksel dosyanın hard-link/yol alias'ı config'te
    reddedilir (os.path.samefile).
Testler: yazma/CREATE reddi, eksik yolda dosya yaratılmaması, eski/yabancı
  şemanın değişmemesi, okuyucu açıkken normal yazmanın sürmesi, WAL
  snapshot'ı, rollback modunda yazanın bloklanması, özel karakterli yol,
  komutlardan sonra girdi DB bayt+mtime eşitliği ve -wal/-shm/-journal
  dosyası oluşmaması.
```

### 18.3 Public smoke durum sözleşmesi (rapor şeması v2)

```
Doğrulama (koddan): karşılaştırma resmî timestamp T ile close_time == T olan
  gözlemi eşler; resmî taraf T'deki snapshot (futuresPrice/indexPrice),
  yerel taraf [T-1h, T) kapanışıdır; oran farkı close_basis_rate −
  basis/indexPrice (34 basamak). 1 bp tolerans DEĞİŞMEDİ.
Rapor v2: kontrol {name, status, category, detail}; status passed | failed |
  skipped | warning; category execution | data_integrity | formula |
  descriptive | anomaly | general; üst düzeyde warning_count.
  "failed" (her kategoride) veya hata -> rapor failed, exit 1.
  "warning" raporu düşürmez (exit 0) ama sayılır, stdout'ta
  "succeeded with N warning(s)" yazılır ve Markdown'da görünür.
Sembol başına kontroller: execution (HTTP/API hatası, 429 dahil; timeout
  sonrası sınırlı retry tükenmesi; parse; istek bütçesi; provenance/coverage
  -> failed), candles_complete ve official_records_complete
  (data_integrity, eksikse failed), official_algebra (formula, tutarsızsa
  failed), official_open_snapshot (anomaly, resmî fiyat T'deki mum
  açılışına eşit değilse warning), close_vs_snapshot_tolerance
  (descriptive, aşım varsa warning; tüm aşım zamanları listelenir).
Bir sembol başarısızsa hatası kaydedilir, diğer sembolün sonuçları korunur,
  rapor failed kalır. İstek bütçesi sembol başına 6 (3 uç nokta x 2 deneme),
  aşılırsa ek istek gönderilmeden durur.
v1 raporları (2026-09-23 gecesi, §17.7) olduğu gibi kalır; o raporun
  "failed" durumu v1 sözleşmesine göre doğruydu.
Testler: mocked transport ile temiz akış, betimsel aşım, anomali, cebirsel
  tutarsızlık, eksik mum, HTTP 500, 429, timeout/retry tükenmesi, bütçe,
  iki sembolden birinin başarısızlığı, CLI exit kodları ve üzerine
  yazmama; beklenen değerler elle türetildi.
```

### 18.4 ETHUSDT'deki 7 aşımın teşhisi (POST-HOC)

```
Girdi: gece artifact'leri (night/public_smoke/ETHUSDT/contract.db, index.db;
  read-only açıldı) + resmî kayıtlar için TEK sınırlı read-only istek
  (2026-09-24T12:49Z; gece yanıtı saklanmamıştı). Yeniden çekilen kayıtlar
  artifact mumlarıyla aynı 7 aşım zamanını üretti; bu, gece yanıtının
  birebir aynısı olduğunu KANITLAMAZ, yalnız tutarlıdır.
Bütünlük: 168/168 contract ve index mumu, boşluk/hizalama sorunu yok;
  resmî 168 kaydın 168'inde futuresPrice = contract open(T) ve
  indexPrice = index open(T).
Ayrıştırma: close_basis − official_basis = (C_close(T-1h) − F(T)) −
  (I_close(T-1h) − I(T)), F = futuresPrice, I = indexPrice.
  T (UTC)           basis farkı  contract bileşeni  index bileşeni  oran farkı
  09-16 11:00        0.62860465        0.00          -0.62860465    0.000261348
  09-16 15:00        0.39488373        0.00          -0.39488373    0.000165287
  09-16 19:00        0.42372093       -0.01          -0.43372093    0.000176661
  09-16 23:00        0.29604651        0.00          -0.29604651    0.000123681
  09-17 15:00       -0.27116279       -0.01           0.26116279   -0.000110083
  09-17 16:00        0.26581396       -0.01          -0.27581396    0.000107582
  09-17 17:00        0.37953488        0.00          -0.37953488    0.000153639
  (index bileşeni = I_close(T-1h) − I_open(T); contract bileşeni en fazla 1 tick)
Yuvarlama: karşılaştırma resmî basisRate'i değil basis/indexPrice'ı kullanır;
  basisRate'in 4 ondalık gösterim artığı (en fazla 0.0000485) farka girmez.
Bulgu: 7 aşımın tamamında fark, index mumunun kapanışı ile bir sonraki index
  mumunun açılışı arasındaki sıçramadan gelir (0.26–0.63); contract tarafı
  0 veya 1 tick. BTC teşhisi (§15.9) kopyalanmadı; aynı sonuç ETH verisinden
  bağımsız olarak elde edildi.
Belirsizlik: index kline'ın kapanış/açılış değerlerinin hangi örneklerden
  üretildiği resmî olarak belgelenmemiştir; sıçramanın mekanizması
  AÇIKLANMADI, yalnız ölçüldü.
```

## 19. Çok Bacaklı Muhasebe/Execution — TASLAK (DRAFT, LOCKED DEĞİL)

**Durum:** DRAFT. Bu bölüm §16'nın tasarım taslağıdır; hiçbir karar kalıcı olarak kilitlenmemiştir. İstisna: §19.10.1'deki ilk muhasebe dilimi IMPLEMENTED + TESTED'dır (yalnız o dilime ait geçici sınırlarla). Örnekler tasarım örnekleridir; çalışan motor veya kârlılık kanıtı değildir. Borsa kuralına dayanan sayısal değer (marj oranları, likidasyon formülü, spot borç faizi, fee seviyeleri) BURADA İDDİA EDİLMEZ; ilgili yerler açık karar olarak işaretlidir.

### 19.1 Mevcut kod (tekrar tasarlanmaz)

```
backtest/accounting.py   AccountState(cash, position_quantity, average_entry_price,
                         realized_pnl); apply_fill (açılış/tam kapanış/tam dönüş);
                         apply_cash_cost; unrealized_pnl; equity = cash + qty * mark.
backtest/execution.py    target_quantity_for (LONG/FLAT/SHORT -> ±qty), bir sonraki
                         mumun OPEN'ında execute_target_on_next_candle.
backtest/costs.py        CostModel(quantity, execution_price) -> Decimal; Composite.
funding/calculator.py    LinearFundingModel: cost = signed_qty * reference_price * rate
                         (pozitif = nakit çıkışı; FUNDING_SPEC §4.3, Binance tanımı §3).
backtest/replay.py       tek sembol; funding sweep fill'den önce, fill öncesi
                         pozisyonla ("event_time <= mark_time"); son mumda fill yok.
validation/metrics.py    Stage-1/2 yalnız BacktestResult'ın equity_curve'ünü okur.
storage/datasets.py      CandleDataset: spot_trade, contract_trade, index_price ... —
                         veri rolleri zaten ayrık.
```
Mevcut tek bacaklı motorda perpetual, spot gibi tam notional nakitle alınıp satılır (`cash -= qty * price`); equity sayısal olarak doğru PnL verir ama nakit bakiyesi notional ile şişer/eksilir (§19.5 Örnek 1).

### 19.2 Enstrüman ve veri rolleri

```
Trade edilebilir bacaklar (önerilen kimlik): (exchange, market_type, symbol)
  spot bacağı:      ("binance", "spot", "BTCUSDT")            fiyat: spot_trade klines
  perpetual bacağı: ("binance", "usdm_perpetual", "BTCUSDT")  fiyat: contract_trade klines
Veri rolleri (trade EDİLEMEZ): index_price (close basis feature'ı, §15),
  mark_price (funding referansı; resmî funding kaydındaki markPrice zaten
  saklanıyor; mark klines ingestion yok). Hiçbiri fill fiyatı olamaz.
```

### 19.3 Önerilen ledger ayrımı

```
LegFill (bacak başına ayrı kayıt): leg_id, time, signed_quantity,
  execution_price, cost, fill_state (filled / partial / unfilled).
SpotLedger: spot_cash, spot_quantity (>= 0 varsayılan), average_entry,
  realized_pnl; değer = spot_cash + spot_quantity * spot_mark.
  (Mevcut AccountState/apply_fill bu semantiği zaten taşır -> yeniden kullanım.)
PerpLedger: collateral (cüzdan bakiyesi), perp_quantity (işaretli),
  average_entry, realized_pnl; değer = collateral + (mark − entry) * qty.
  Notional HİÇBİR ZAMAN nakde/collateral'a eklenmez; fill yalnız fee'yi
  collateral'dan düşer, kapanışta gerçekleşen PnL collateral'a yazılır.
  (apply_fill perp için doğrudan kullanılamaz: cash −= qty * price yapar;
  ayrı, küçük bir perp geçiş fonksiyonu gerekir.)
Funding: yalnız PerpLedger'a, LinearFundingModel ile, settlement anında
  tutulan perp pozisyonuyla, tam bir kez (mevcut sweep kuralı).
Maliyet: bacak başına ayrı CostModel (spot ve perp fee'leri farklı olabilir);
  mevcut CostModel arayüzü aynen kullanılabilir.
Portföy equity = SpotLedger değeri + PerpLedger değeri. Aradaki transferler
  (spot_cash <-> collateral) iç harekettir, equity'yi değiştirmez.
Metrik entegrasyonu: portföy equity eğrisi mevcut EquityPoint/BacktestResult
  biçimine indirgenirse Stage-1/2 değişmeden çalışır; bacak ayrıntısı ek alan
  olarak taşınmalı (açık karar K6).
```

### 19.4 Zamanlama

```
Karar: mum kapanışında (feature_availability_time), iki bacağın da
  kullanılabilir verisiyle (spot + contract; index yalnız feature).
Fill: bir sonraki mumun OPEN'ı (mevcut kural) — iki bacak için ayrı mum
  serileri; iki OPEN aynı an etiketlidir ama aynı fiyat anı değildir.
Funding: settlement anındaki perp pozisyonuna, fill'den önce.
Partial fill / legging: iki bacağın dolum durumu ayrı kaydedilir; biri dolup
  diğeri dolmazsa hedge VARSAYILMAZ (Örnek 6). Politikası açık karar K3.
```

### 19.5 Elle hesaplanabilir örnekler (tasarım örneği; fee oranları yalnız örnektir)

```
Başlangıç: spot_cash 9000, collateral 1000 (toplam 10000). Spot fee %0.1,
  perp fee %0.05 (varsayım, doğrulanmış borsa fee'si değil).
Örnek 1 — düz fiyat + maliyet: 1 spot al @100 (fee 0.1), 1 perp short @100.5
  (fee 0.05025). spot_cash 8899.9, spot_qty 1; collateral 999.94975,
  perp_qty −1 @100.5. Fiyatlar aynı: equity = 8899.9 + 100 + 999.94975 + 0
  = 9999.84975 = 10000 − 0.15025. Mevcut tek bacaklı muhasebe perp'i nakit
  gibi işlerse collateral 1100.44975 görünür (+100.5 notional) — equity aynı,
  ama nakit/collateral ve getiri paydası yanlış: bu ÇİFT SAYIM riskidir.
Örnek 2 — birlikte hareket: S 100->110, P 100.5->110.5 (basis 0.5 sabit):
  spot 8899.9 + 110 = 9009.9; perp 999.94975 + (110.5−100.5)(−1) = 989.94975;
  toplam 9999.84975 (değişmez).
Örnek 3 — basis değişimi: S 110, P 110.2 (basis 0.5 -> 0.2): perp
  unrealized −9.7; toplam 10000.14975 = başlangıç + 0.3 basis daralması −
  0.15025 maliyet.
Örnek 4 — funding (reference 110.2): oran +0.0001 -> cost = (−1)(110.2)
  (0.0001) = −0.01102 (short alır, collateral +0.01102); oran −0.0001 ->
  +0.01102 (short öder). Spot bacağına funding YOK.
Örnek 5 — kapanış (Örnek 3 + pozitif funding sonrası): spot sat @110 (fee
  0.11) -> spot_cash 9009.79; perp al @110.2 (fee 0.0551), gerçekleşen
  (100.5−110.2)*1 = −9.7 -> collateral 999.94975 + 0.01102 − 9.7 − 0.0551
  = 990.20567. Toplam 9999.99567 = 10000 − 0.31535 maliyet + 0.3 basis +
  0.01102 funding.
Örnek 6 — yalnız bir bacak dolar: spot alındı (8899.9, 1 adet), perp short
  dolmadı; S 100->90: equity 8899.9 + 90 + 1000 = 9989.9 — hedge'siz −10
  fiyat kaybı + 0.1 fee. Motor bunu "hedge'li" göstermemeli.
(Değerler Decimal ile ayrıca yeniden hesaplanıp doğrulandı.)
```

### 19.6 Spot short / borç senaryoları

```
Spot long + perp short: borç gerektirmez (bu taslağın kapsamı).
Spot short + perp long (negatif basis carry): spot varlık ödünç alınmasını
  gerektirir (margin/borrow). Borç erişimi, faiz, geri çağırma riski ve
  teminat kuralları doğrulanmadı -> ilk dilimde KAPSAM DIŞI; ayrı karar (K4).
```

### 19.7 Hedge oranı ve miktar hassasiyeti

```
Varsayılan öneri: miktar bazında 1:1 (spot_qty = |perp_qty|). Notional veya
  beta bazlı oran açık karar (K2). Borsa lot/step/tick kuralları (miktar
  yuvarlama) doğrulanmadı; ilk dilimde miktarlar config'ten Decimal olarak
  verilir ve yuvarlanmaz, kural doğrulanınca ayrı mikro-adım (K5).
```

### 19.8 Margin/likidasyon sınırları

```
İlk dilimde likidasyon MODELLENMEZ; yalnız "collateral + unrealized <= 0"
  anında çalıştırma hata ile durur (sessizce negatif collateral yok).
Başlangıç/bakım marjı, kaldıraç kademeleri ve likidasyon fiyatı resmî
  kaynakla doğrulanmadan varsayılan davranış olarak kodlanmaz (K1).
```

### 19.9 Geriye uyumluluk

```
Mevcut tek bacaklı motor (replay, store_runner, rolling, BacktestResult,
  funding-carry araştırması) DEĞİŞMEZ; çok bacaklı motor ayrı modül
  (ör. backtest/multileg.py) olarak eklenir, mevcut CostModel, FundingModel,
  AccountState (spot bacağı için) ve dataset/provenance katmanını kullanır.
```

### 19.10 Önerilen ilk uygulanabilir dilim

```
Kapsam: iki bacak (spot long + perpetual short, 1:1 miktar), tek sembol,
  tam dolum varsayımı AÇIKÇA işaretli (partial fill yok), bacak başına
  CostModel, yalnız perp'e LinearFundingModel, sabit giriş/çıkış zamanlarıyla
  (strateji/politika yok) deterministik muhasebe + portföy equity eğrisi;
  §19.5 örnekleri birebir acceptance testleri. Likidasyon yok (collateral
  <= 0 -> hata). Spot short yok. Karar/sinyal katmanı yok.
Önkoşul: spot bacağı için provenance'lı spot store (spot ingestion var;
  provenance kaydı spot için eklenmeli) ve K1–K6 kararları.
```

### 19.10.1 İlk dilim — IMPLEMENTED + TESTED (2026-09-24)

§19 bütünü DRAFT olarak kalır; yalnız bu ilk muhasebe dilimi uygulanmış ve test edilmiştir. Bu bir basis/carry stratejisi DEĞİLDİR; kârlılık sınanmamıştır; index trade edilmemiştir; liquidation ve legging modellenmemiştir; çok bacaklı replay/event scheduling YOKTUR. Faz 7 bütünü tamamlanmamıştır; FAZ6C tamamlanmamıştır; FAZ6D başlamamıştır.

```
Modül: src/crypto_quant_lab/backtest/multileg.py (additive; tek bacaklı motor,
  AccountState, BacktestResult, replay, store/rolling runner, funding-carry,
  CLI/rapor sözleşmeleri DEĞİŞMEDİ ve bu modülü import etmez).
Public API:
  LegSide {BUY, SELL}; HedgeLifecycle {FLAT, HEDGED_OPEN, FLAT_CLOSED}
  TradableInstrument(exchange, market_type in {spot, usdm_perpetual}, symbol,
      quote_asset); .from_candle_dataset(dataset, quote_asset=) — yalnız
      spot_trade / contract_trade kabul; index_price, mark_price vb. reddedilir
  HedgedPair(pair_id, spot, perpetual, hedge_ratio=1)  # açık eşleşme, sembol
      parse edilmez; aynı exchange ve quote asset; hedge_ratio yalnız 1
  LegExecution(instrument, side, quantity>0, price>0, time aware)
  LegFill(execution, cost); PairedFill(action "open"|"close", time, spot, perpetual)
  SpotLedger(cash, quantity, entry_price, realized_pnl, costs_paid)
  PerpetualLedger(collateral, quantity<=0, entry_price, realized_pnl,
      costs_paid, funding_paid)
  HedgedPortfolioState(pair, lifecycle, initial_spot_cash,
      initial_perpetual_collateral, spot, perpetual, fills,
      applied_funding_keys, last_event_time); initial_total_capital;
      liquidation_not_modeled == True
  new_hedged_portfolio(pair, *, spot_cash, perpetual_collateral)
  apply_hedged_open(state, *, spot, perpetual, spot_cost_model, perpetual_cost_model)
  apply_perpetual_funding(state, event: HistoricalFundingEvent, *, funding_model)
  apply_hedged_close(state, *, spot, perpetual, spot_cost_model, perpetual_cost_model)
  mark_hedged_portfolio(state, *, time, spot_mark_price, perpetual_mark_price)
      -> HedgedPortfolioMark (görünüm; state değişmez)
  summarize_closed_hedge(state) -> HedgedLifecycleSummary
      (liquidation_not_modeled, legging_not_modeled == True)
Muhasebe:
  spot açılış:  cash -= q * p_s + c_s;  quantity += q;  entry = p_s
  spot kapanış: cash += q * p_s' - c_s'; realized += q * (p_s' - entry); quantity = 0
  spot değeri:  cash + quantity * spot_mark
  perp açılış:  collateral -= c_p (notional nakit DEĞİL); quantity = -q; entry = p_p
  perp funding: cost = FundingModel(signed_qty, reference_price, rate) (mevcut
                LinearFundingModel; pozitif ödenir); collateral -= cost
  perp kapanış: realized += q * (entry - p_p'); collateral += realized - c_p'
  perp unrealized = (mark - entry) * signed_qty; margin equity = collateral + unrealized
  collateral == initial + realized - costs - funding (her geçişte doğrulanır)
  portföy equity = spot cash + spot qty * spot mark + collateral + perp unrealized
  total PnL = equity - (initial spot cash + initial collateral)
Çift sayım önlemleri: perp notional hiçbir deftere yazılmaz; index/mark
  fiyatı fill olamaz; maliyet ve funding yalnız kendi defterinden bir kez
  düşer; her mark'ta bakiye tabanlı (equity - initial) ile atıf tabanlı
  (realized + unrealized - maliyet - funding, bacak başına) PnL eşit olmak
  zorundadır, değilse ValueError (fail-fast).
Atomiklik: geçişler saf ve immutable (yeni state döner). Eksik bacak,
  eşit olmayan miktar, farklı timestamp, yanlış yön (spot short / perp long),
  yanlış enstrüman, geçersiz maliyet çıktısı (float, negatif, NaN), yetersiz
  spot nakit, ikinci açılış, açılmadan kapanış, kısmi kapanış -> ValueError
  veya TypeError, önceki state aynen kalır, fill kaydı oluşmaz.
Zaman: tüm olaylar kesin artan (open < funding < close); açılış veya kapanış
  anındaki funding belirsiz sayılıp reddedilir (sıra gelecekteki replay
  sözleşmesine bırakıldı); mark zamanı son olaydan önce olamaz; eşdeğer
  timezone anları tek an sayılır.
Decimal: aritmetik tek bacaklı motor gibi çağıranın context'inde çalışır
  (COST_MODEL_SPEC §20); araştırma çağrıları research/decimal_policy.py ile
  açık context kullanır. Test: prec=4 ortamında aynı senaryo "closed hedge
  invariant violated" ile durur (sessiz yanlış sonuç yok); açık context
  içinde sonuç varsayılan context'tekiyle birebir aynıdır.
```

Bu dilime ait geçici sınırlar (K1–K7 kalıcı olarak KİLİTLENMEDİ):

```
K1 liquidation modeli yok; negatif margin equity gösterilir
   (liquidation_not_modeled). Maintenance margin, kaldıraç kademesi,
   liquidation fiyatı yok. Not: §19.8'deki "collateral + unrealized <= 0 ->
   hata" taslak önerisi bu dilimde kullanıcı kararıyla UYGULANMADI.
K2 yalnız sabit 1:1 base quantity (spot q, perp -q); eşitsiz miktar reddedilir.
K3 atomik eşli fill; tek bacak, partial fill, farklı zaman reddedilir.
K4 yalnız spot long + perp short; spot miktarı negatife düşemez.
K5 exchange lot/step/tick quantization yok; girdi Decimal'ler aynen kullanılır.
K6 ayrı sonuç tipleri (HedgedPortfolioMark, HedgedLifecycleSummary);
   BacktestResult'a paketlenmez; Stage-1/2 metrik entegrasyonu yok.
K7 iki sabit cüzdan (spot_cash, perpetual_collateral), aynı quote asset;
   transfer ve cross-wallet collateral yok.
```

Acceptance eşlemesi (`tests/test_backtest_multileg.py`, 22 test; beklenenler elle türetildi):

```
A  açılışta çift sayım yok: equity 400 (500 değil)     test_a_open_does_not_double_count_the_perpetual_notional
B  iki piyasa +10: spot 210, perp unrealized -10, 400  test_b_both_markets_rise_and_the_hedge_holds
C  basis yakınsaması: +1 / +1, final 402              test_c_basis_convergence_realizes_both_legs
D  bacak başına maliyet: spot 0.2, perp 0.1, final 399.7  test_d_costs_hit_each_ledger_exactly_once
E  funding ±0.0001 @100: collateral 200.01 / 199.99, tekrar reddi  test_e_funding_moves_only_the_perpetual_ledger_once
F  tam kapanış: cüzdanlar 201 + 201 = 402, her mark'ta test_f_full_close_leaves_only_the_two_wallets
G  atomik ret: eksik bacak, miktar, zaman               test_g_invalid_pairs_are_rejected_atomically
H  yön/oran/tradability sınırları                       test_h_direction_ratio_and_tradability_limits
Ek: model doğrulama, ayrı cüzdan ve yetersiz nakit, geçersiz maliyet çıktısı,
    lifecycle/zaman kuralları, eşdeğer timezone, yabancı funding olayı,
    farklı bacak hareketleri (basis genişleme -2 / daralma +1), negatif margin
    equity gösterimi (-15), saflık/tekrarlanabilirlik, açık Decimal context.
§19.5'teki 6 taslak örneği bu testlerde birebir değil; kullanıcının A–H
senaryoları acceptance olarak kullanıldı.
```

Gelecek işler (PENDING): çok bacaklı replay/event scheduling, partial fill/legging, margin/liquidation modeli, spot short/borrow, hedge oranı genelleştirmesi, lot/tick adapter'ı, portföy sonucunun metrik katmanına açık adapter sözleşmesi, basis/carry strateji değerlendirmesi.

### 19.11 Açık kararlar

```
K1 margin/likidasyon modeli ve kaynağı (Binance USDⓈ-M kuralları doğrulanmalı)
K2 hedge oranı: miktar mı, notional mı, dinamik mi
K3 legging/partial fill politikası: tek bacak dolunca iptal mi, bekle mi, zorla mı
K4 spot short/borrow kapsamı
K5 lot/step/tick yuvarlama kuralları ve kaynağı
K6 portföy sonucunun BacktestResult'a indirgenmesi mi, yeni sonuç tipi mi
K7 spot ve perp cüzdanları arası transfer modeli (tek havuz mu, ayrık mı)
```

### 19.12 Çok bacaklı replay / event-scheduling sözleşmesi — DRAFT, NOT IMPLEMENTED

[2026-09-25 güncellemesi: bu bölüm genel olarak DRAFT kalır; ilk in-memory replay dilimi, E1 ve sentetik offline demo §19.12.8'de IMPLEMENTED + TESTED. Aşağıdaki 2026-09-24 taslak metni tarihsel olarak korunmuştur.]

**Durum (2026-09-24):** DRAFT. Kod veya test YOKTUR; aşağıdaki kabul matrisi çalıştırılmış test DEĞİLDİR. §19.10.1 (muhasebe ilk dilimi) IMPLEMENTED + TESTED olarak kalır. Hiçbir yeni ekonomik karar burada kilitlenmez; her satırın karar durumu ayrıca yazılmıştır.

#### 19.12.1 Mevcut tek bacaklı zamanlama sözleşmesi (kanıtla)

```
Kavram                     Kural                                           Kanıt
mum aralığı                [open_time, open_time + süre)                   market_data/timeframes.py candle_duration;
                                                                           data_quality/feature_availability.py
erişilebilirlik (bilgi)    availability = open_time + süre; OPEN dahil tüm BACKTEST_SPEC §8; feature_availability_time;
                           OHLCV bu andan önce kullanılamaz; sınır dahil   is_candle_available_for_features (>=)
                           (as_of >= availability)
veri kümesi                tek sembol+timeframe, kesin artan open_time,    replay.py _validate_dataset (satır 74);
                           boşluksuz bitişik (availability(N) ==          BACKTEST_SPEC §30, §31
                           open_time(N+1)), her mum as_of'ta erişilebilir
karar                      mum N'nin availability anında, yalnız o ana    replay.py (policy çağrısı, satır ~397);
                           kadar erişilebilir önek (PolicyContext)        BACKTEST_SPEC §9
fill                       N'nin kararı N+1'in OPEN fiyatından, zamanı    execution.py execute_target_on_next_candle;
                           open_time(N+1) == availability(N)              BACKTEST_SPEC §10; acceptance §36 madde 10
son mum                    son mumun kararı fill OLMAZ, fiyat uydurulmaz   replay.py (i == last_index, satır ~403);
                                                                           BACKTEST_SPEC §11
equity mark                her değerlendirme mumunda candle.CLOSE ile;     replay.py satır ~394; BACKTEST_SPEC §18;
                           mark, o anın kendi fill'inden ÖNCE              FUNDING_SPEC §13
funding settlement zamanı  event_time; muhasebeyi yalnız o anda etkiler;   FUNDING_SPEC §11
                           event_time > as_of uygulanamaz
aynı an sırası (T)         1) funding settlement(ler) 2) equity mark       FUNDING_SPEC §12 (LOCKED MS9/MS10);
                           3) policy 4) fill; funding PRE-FILL pozisyona   replay.py _apply_due_funding_events (satır 250);
                                                                           test_same_time_open_from_flat_pays_no_funding,
                                                                           test_same_time_close_to_flat_uses_held_position,
                                                                           test_same_time_long_to_short_reversal_uses_pre_fill_position
mumlar arası funding       kronolojik uygulanır, sonraki mark'ta görünür;   FUNDING_SPEC §13; test_long_positive_funding_between_marks
                           kendi EquityPoint'i yok
aynı anda çoklu funding    her biri ayrı, (event_time, rate_type) artan,   FUNDING_SPEC §12; replay.py satır ~197;
                           hepsi aynı pre-fill pozisyonu görür             test_same_time_multiple_funding_events_applied_sequentially,
                                                                           test_funding_event_same_time_rate_type_descending_raises
kimlik / tekrar            canonical_key (exchange, market_type, symbol,  funding/models.py canonical_key; replay.py satır ~207;
                           event_time, rate_type); tekrar -> ValueError    test_duplicate_funding_event_key_raises
funding aralığı            run_start <= event_time < run_end;             replay.py _validate_funding_events;
                           run_start = evaluation_start (yoksa ilk open), test_funding_at_run_start_is_legal_and_zero_when_flat,
                           run_end = availability(son mum); dışı -> hata  test_funding_before_run_start_is_rejected,
                           (filtrelenmez)                                  test_funding_at_run_end_is_rejected
düz pozisyonda funding     formülle 0 (özel dal yok)                       FUNDING_SPEC §8.2; test_flat_funding_event_is_zero_cost
warmup/evaluation_start    öncesindeki mumlar yalnız bağlam: funding,      replay.py modül docstring; VALIDATION_SPEC §8.3
                           mark, policy, fill yok; state o anda başlar
funding referans fiyatı    mark fiyatı DEĞİLDİR                            FUNDING_SPEC §13
```

İki zaman AYRIDIR: bir nakit hareketinin muhasebeye ait olduğu **settlement zamanı** (`FundingEvent.event_time`, replay'de `event_time <= mark_time` taraması) ile bir verinin karar için bilinebildiği **availability zamanı** (mum: `open_time + süre`; funding sinyali: `event_time + publication_lag`, research/funding_carry.py FundingSignalHistory). Replay muhasebesi yayın gecikmesi uygulamaz; sinyal görünürlüğü ise yalnız research katmanında vardır. Funding olaylarının muhasebe için ayrı bir "availability" metadata'sı YOKTUR — varmış gibi kabul edilmez.

Belge/kod farkı: bulunmadı. FUNDING_SPEC §12'deki "fill" adımı kodda mum N iterasyonunda open(N+1) fiyatıyla, zaman damgası availability(N) olan fill'dir; sıra birebir aynıdır.

#### 19.12.2 Karşılaştırma

```
Yeniden kullanılır: feature_availability_time; _validate_dataset kuralları (her seri için);
  MS9 aynı-an sırası; funding aralık/sıra/kimlik doğrulaması; LinearFundingModel;
  CostModel; §19.10.1 muhasebe API'si; decimal_policy açık context'i.
Yeni tanımlanmalı: iki serinin senkronizasyonu; dışarıdan deterministik "intent"
  sözleşmesi; olay izi (trace) ve çıktı tipi; düz pozisyonda funding kaydı.
İlk muhasebe API'siyle çakışan: _require_after_last_event (multileg.py satır 303)
  aynı zaman damgasındaki İKİNCİ state-değiştiren olayı reddeder; bu, MS9'un
  "funding(T) sonra fill(T)" ve "aynı T'de çoklu funding" kurallarıyla çakışır.
  apply_perpetual_funding FLAT/FLAT_CLOSED'da reddeder (satır 470); tek bacaklı
  motor ise düz pozisyonda formülle 0 uygular.
Bu adımın ve ilk replay'in dışında: store-backed runner, strateji/policy çerçevesi,
  warmup/evaluation_start, legging/partial fill, margin/liquidation, metrik adapter'ı.
```

#### 19.12.3 İlk replay için dar taslak

```
A) Girdiler
  pair: HedgedPair (§19.10.1); spot_candles: tuple[Candle] (spot_trade dataset'ten),
  perpetual_candles: tuple[Candle] (contract_trade dataset'ten); aynı timeframe;
  funding_events: tuple[HistoricalFundingEvent] (yalnız pair.perpetual partition'ı);
  initial spot_cash, perpetual_collateral; spot/perp CostModel; FundingModel; as_of;
  intents: tuple[HedgeIntent(action "open"|"close", decision_time, quantity)].
  Provenance (spot_trade / contract_trade) store'dan okunurken zaten doğrulanır;
  in-memory replay'de Candle provenance taşımaz — çağıran sorumludur (açık not).
B) Senkronizasyon
  Her seri tek başına _validate_dataset kurallarını geçer (mevcut). İki serinin
  open_time kümeleri BİREBİR eşit olmalı; aksi halde tüm çalıştırma reddedilir
  (eksik mum, farklı timeframe, sırasız/duplicate -> hata). Forward-fill,
  interpolasyon, tolerans süresi YOK. [Taslak öneri: tek bacaklı "boşluksuz"
  kuralının iki seriye doğal uzantısı; kısmi boşlukla çalışma açık karar R1.]
C) Karar / execution ayrımı
  Intent'in decision_time'ı bir mumun availability anına (N < son) eşit olmalı;
  fill iki serinin N+1 OPEN fiyatlarından, zaman damgası decision_time.
  Intent fiyat taşımaz. Son mumdaki intent reddedilir (tek bacaklı §11 kuralı:
  fill yok, fiyat uydurulmaz). Replay intent'in içeriğinin hangi bilgiyle
  üretildiğini doğrulayamaz; yalnız mekanik zamanlamayı garanti eder. Strateji
  çerçevesi gerekmez; policy katmanı sonraki bir dilimdir.
D) Fiyat kaynakları
  Execution: spot_trade ve contract_trade mumlarının OPEN'ı. Valuation (mark):
  aynı mumların CLOSE'u (tek bacaklı §18 ile aynı). mark_hedged_portfolio'nun
  spot_mark_price/perpetual_mark_price parametreleri "değerleme fiyatı" demektir,
  borsanın markPrice dataset'i DEĞİLDİR; mark-price ingestion yapılmadı.
  Index/mark fiyatları fill olamaz (TradableInstrument zaten reddeder).
E) Çıktılar (öneri, sahte BacktestResult YOK, Stage-1/2 adapter'ı YOK)
  MultiLegReplayResult: trace (her olay: time, phase {funding, mark, fill},
  ayrıntı), paired_fills, funding_records (canonical_key, gözlenen perp
  pozisyonu, maliyet), equity_points (time, spot_equity, perp_margin_equity,
  portfolio_equity, total_pnl), final_state, final_mark, unexecuted_final_intent,
  kapsam bayrakları (liquidation_not_modeled, legging_not_modeled,
  valuation_source "trade_close").
  Çalıştırma sonunda pair açık kalabilir (tek bacaklı motorda pozisyon açık
  bitebilir); o durumda summarize_closed_hedge değil son mark raporlanır.
  [Taslak öneri.]
```

#### 19.12.4 Aynı zaman damgası uyumluluğu

`last_event_time` yalnız open, funding ve close ile ilerler (multileg.py satır 402, 458, 504); `_require_after_last_event` KESİN büyüklük ister (satır 303–307); mark bir görünümdür, state'i değiştirmez ve `time >= last_event_time` yeter (satır 548). `applied_funding_keys` canonical_key'leri tutar (satır 484, 503); lifecycle kontrolleri satır 367, 417, 470.

```
Durum: funding(T) + açılış(T)
 1 tek bacaklı: funding PRE-FILL (düz) pozisyona -> 0; sonra fill        (test_same_time_open_from_flat_pays_no_funding)
 2 multileg API: FLAT'ta apply_perpetual_funding reddeder; açılış tek başına geçer
 3 öneri: scheduler olayı izde kaydeder, gözlenen pozisyon 0, maliyet FundingModel(0, ...) = 0;
          muhasebe API'si çağrılmaz; sonra açılış
 4 mevcut API ile: EVET
 5 değişiklik: yok (istenirse ileride FLAT funding kaydı için additive API)
 6 karar: ekonomik sonuç mevcut kuralla belirli (0); kayıt biçimi taslak öneri

Durum: funding(T) + kapanış(T)
 1 tek bacaklı: funding açık (short) pre-fill pozisyona uygulanır, sonra fill (test_same_time_close_to_flat_uses_held_position)
 2 multileg API: funding(T) sonrası kapanış(T) "strictly after" ile reddedilir; kapanış önce yapılırsa funding FLAT_CLOSED'da reddedilir
 3 öneri: MS9 sırası — önce funding (short'a), sonra kapanış
 4 mevcut API ile: HAYIR
 5 en küçük additive değişiklik E1: olay sıralama anahtarı (zaman, faz[, rate_type]) —
   faz FUNDING < FILL; state'e varsayılanlı yeni alan (ör. last_event_key); aynı T'de
   funding -> fill izinli, fill -> funding reddedilir; eski davranış farklı zamanlarda aynı
 6 karar: MS9'un çok bacaklıya uygulanması TASLAK ÖNERİ (tek bacaklı kural kilitli,
   çok bacaklıya uzatılması kullanıcı onayı ister); onaylanmazsa ilk replay bu durumu
   §19.10.1 gibi reddeder

Durum: aynı T'de farklı canonical_key'li iki funding (ör. Regular + Special)
 1 tek bacaklı: ikisi ayrı, (event_time, rate_type) artan, aynı pre-fill pozisyona (test_same_time_multiple_funding_events_applied_sequentially)
 2 multileg API: ikincisi "strictly after" ile reddedilir
 3 öneri: tek bacaklıdaki sıra
 4 mevcut API ile: HAYIR
 5 değişiklik: E1'in rate_type bileşeni (zaman, FUNDING, rate_type) kesin artan
 6 karar: taslak öneri (E1 ile aynı onaya bağlı)

Durum: aynı funding olayının tekrarı
 1 tek bacaklı: girişte duplicate canonical_key -> ValueError (test_duplicate_funding_event_key_raises)
 2 multileg API: "already applied" ile reddeder
 3 öneri: scheduler girişte (hiçbir olay uygulanmadan) reddeder
 4 mevcut API ile: EVET
 5 değişiklik: yok
 6 karar: mevcut kuralla belirli

Durum: aynı kimlikte çelişkili payload (aynı key, farklı rate/reference_price)
 1 tek bacaklı: duplicate key olarak reddedilir (payload karşılaştırılmaz); store yazımı DataConflictError verir
 2 multileg API: ikincisi "already applied" (tekrar ile çelişki ayırt edilmez)
 3 öneri: scheduler girişte reddeder; mesajda "conflicting payload" ile "duplicate" ayrılır
 4 mevcut API ile: EVET (scheduler doğrulaması)
 5 değişiklik: yok
 6 karar: ret mevcut kuralla belirli; mesaj ayrımı taslak

Durum: mark ile state değiştiren olayın aynı T'de olması
 1 tek bacaklı: T'de funding -> mark -> policy -> fill; mark fill'i içermez, funding'i içerir (FUNDING_SPEC §12-13)
 2 multileg API: mark görünümdür; T'deki funding'den sonra (>=) izinli; T'deki fill'den önce çağrılabilir
 3 öneri: aynı sıra: funding(ler), mark (fill öncesi state), fill
 4 mevcut API ile: EVET
 5 değişiklik: yok
 6 karar: mevcut kuralla belirli
```

Sonuç: yalnız scheduler yazmak MS9'un tamamı için YETMEZ; "funding(T) + kapanış(T)" ve "aynı T'de çoklu funding" için E1 gerekir. Zaman damgasına yapay mikro-saniye eklemek, olay kaydırmak, funding atlamak, olayları birleştirmek veya kesin-zaman kontrollerini körlemesine gevşetmek reddedilir.

#### 19.12.5 Gelecek implementasyon için kabul matrisi (TASLAK — çalıştırılmamış)

[2026-09-25: S1–S12 çalıştırılabilir testlere eşlendi ve geçti; S3 ve S5 için R2 (MS9 + E1) seçildi. Eşleme §19.12.8'de.]

Ortak kurulum (S1–S5): 1h mumlar, t0 = 00:00Z, mum k = [t0 + k·h, t0 + (k+1)·h), k = 0..3; run_end = availability(3) = t0 + 4h. Başlangıç: spot_cash 200, collateral 200, qty 1, sıfır maliyet. Intent'ler: open decision t0+1h (mum 0 kapanışı) -> fill mum 1 OPEN (spot 100, perp 102) @ t0+1h; close decision t0+3h -> fill mum 3 OPEN (spot 101, perp 101) @ t0+3h. Kapanışlar (spot/perp): mum0 100/102, mum1 100.5/101.5, mum2 101/101, mum3 101/101.

```
S1 funding yok
   sıra: t0+1h mark(FLAT) -> fill open; t0+2h mark; t0+3h mark -> fill close; t0+4h mark
   equity: 400 (FLAT) | 401 (spot 100+100.5; perp 200+(101.5-102)(-1)) | 402 | 402
   final: spot_cash 201, collateral 201, total_pnl 2   [§19.10.1 C ile aynı; mevcut API yeter]
S2 açıkken mumlar arası funding: t0+2h30m, rate 0.0001, reference 101
   sıra: ... t0+2h mark; funding (short -1) cost = -1*101*0.0001 = -0.0101 (alınır);
         t0+3h mark (funding dahil) -> fill close
   equity t0+3h: 402.0101; final collateral 201.0101, final equity 402.0101
   [mevcut API yeter; scheduler yeni]
S3 kapanışla aynı anda funding: t0+3h, rate 0.0001, reference 101
   sonuç KARARA BAĞLI:
   - MS9 çok bacaklıya uygulanırsa (öneri, E1 gerekir): funding short'a -> +0.0101,
     sonra kapanış; final 402.0101
   - uygulanmazsa: ilk replay çalıştırmayı reddeder (mevcut §19.10.1 davranışı)
   - "önce fill" alternatifi MS9'la çelişir (pozisyon 0 -> funding 0, final 402); önerilmez
S4 açılışla aynı anda funding: t0+1h, rate 0.0001
   MS9: pre-fill pozisyon 0 -> maliyet 0 (izde kayıt), sonra açılış; final 402
   [ekonomi mevcut kuralla belirli; mevcut API ile yapılabilir]
S5 aynı anda iki funding (t0+2h30m): Regular 0.0001 ve Special 0.00005, reference 101
   (Regular < Special sırasıyla) maliyetler -0.0101 ve -0.00505 -> collateral 201.01515,
   final 402.01515   [E1'e bağlı; E1 yoksa ret]
S6 aynı canonical_key iki kez (aynı ya da çelişkili payload) -> girişte ret, hiçbir olay
   uygulanmaz [mevcut kural]
S7 run sınırları: event_time < ilk open -> ret; event_time == t0+4h (run_end) -> ret;
   event_time == t0 (açılıştan önce, FLAT) -> maliyet 0 kaydı [mevcut kural]
S8 kapanıştan sonra aralık içinde funding (FLAT_CLOSED) -> pozisyon 0, maliyet 0 kaydı
   [ekonomi mevcut kural; kayıt biçimi taslak]
S9 senkronizasyon: spot mum 2 eksik / perp farklı timeframe / sırasız veya duplicate ->
   çalıştırmanın tamamı ret, hiçbir fill yok [her seri için mevcut kural; eşitlik yeni]
S10 look-ahead: decision_time mumun availability'si değil (ör. t0+1h30m) -> ret;
   son mumda intent (t0+4h) -> fill yok, "unexecuted_final_intent"; intent fiyat taşıyamaz
   [BACKTEST_SPEC §9-11'in uzantısı]
S11 determinizm: aynı girdiler + decimal_policy açık context -> özdeş trace ve
   sonuç; ortam prec=4 iken açık context içinde yine özdeş [mevcut desen: §18.1,
   test_explicit_research_context_makes_results_independent_of_ambient_context]
S12 lifecycle: ikinci open intent, açılmadan close, 1:1 dışı miktar -> ret
   (§19.10.1 API'si zaten reddeder)
```

#### 19.12.6 Açık kararlar

[2026-09-25: R1–R5 yalnız ilk replay için kullanıcı tarafından karara bağlandı (§19.12.8); genel tasarım için açık kalırlar.]

```
R1 iki serinin kısmi boşlukla çalışması (öneri: tam eşitlik şart; ilk replay'i ENGELLEMEZ)
R2 MS9 aynı-an sırasının çok bacaklıya uzatılması + E1 (engellemez: onay yoksa
   ilk replay aynı-an durumlarını reddeder)
R3 run sonunda açık pair'in raporlanması (öneri: son mark; engellemez)
R4 düz pozisyonda funding kayıt biçimi (engellemez)
R5 warmup/evaluation_start (ilk replay'de yok; policy katmanıyla birlikte)
K1–K7 (§19.11) ilk replay'i engellemez; §19.10.1 geçici sınırları geçerli kalır.
```

#### 19.12.7 Önerilen sonraki TEK implementasyon dilimi

```
Amaç: in-memory, store'suz çok bacaklı replay — iki mum serisi + deterministik
  intent'ler + funding olayları -> MultiLegReplayResult; MS9 sırasıyla.
Dosya sınırı: yeni backtest/multileg_replay.py; backtest/multileg.py'de yalnız E1
  (additive: varsayılanlı sıralama anahtarı alanı; farklı zamanlı davranış aynı);
  yeni test dosyası; spec §19.12 durum güncellemesi. Tek bacaklı motor, runner'lar,
  CLI/rapor sözleşmeleri DEĞİŞMEZ.
Önkoşul: R2 kararı (E1'i dahil et / etme). R1, R3, R4 öneriyle ilerleyebilir.
Kabul: §19.12.5 S1–S12 elle türetilmiş Decimal değerlerle; §19.10.1'in 22 testi ve
  tüm tek bacaklı testler değişmeden yeşil.
```

Durumlar: Multi-leg accounting first slice IMPLEMENTED + TESTED · Multi-leg replay contract DRAFT · Multi-leg replay implementation NOT IMPLEMENTED · Partial fill/legging PENDING · Margin/liquidation NOT MODELED · Basis/carry strategy evaluation PENDING · Faz 7 NOT COMPLETE · FAZ6C NOT COMPLETE · FAZ6D NOT STARTED.

#### 19.12.8 İlk in-memory replay dilimi — IMPLEMENTED + TESTED (2026-09-25)

§19 ve §19.12'nin geri kalanı DRAFT kalır. Bu dilim bir strateji, basis/carry değerlendirmesi veya kârlılık kanıtı DEĞİLDİR; intent'ler dışarıdan verilen scripted girdilerdir.

```
Kullanıcı kararları (yalnız bu ilk replay için; gelecek tasarımı kilitlemez):
  R1 iki seri aynı timeframe ve birebir aynı open_time ızgarası; her seri tek
     bacaklı _validate_dataset kurallarını geçer; forward-fill / tolerans /
     kısmi kesişim YOK -> belirli hata
  R2 MS9 sırası FUNDING -> EQUITY MARK -> INTENT -> PAIRED FILL, funding
     pre-fill pozisyonu görür; E1 (aşağıda) ile
  R3 sonda açık pozisyon açık kalır; son trade CLOSE ile değerlenir; sentetik
     kapanış ve kapanış maliyeti YOK; kapalı özet üretilmez
  R4 FLAT / FLAT_CLOSED'da funding sıfır kayıt (FundingModel qty 0 ile 0),
     accounting API çağrılmaz; global duplicate denetimine dahil
  R5 warmup / evaluation_start YOK (parametre de yok); tüm run değerlendirilir
  K1–K7 (§19.10.1) geçici sınırları aynen.

E1 — backtest/multileg.py (additive, commit 7d68102):
  EventOrdering {STRICT_TIME (varsayılan, §19.10.1 davranışı birebir),
                 FUNDING_BEFORE_FILL}
  new_hedged_portfolio(..., event_ordering=EventOrdering.STRICT_TIME)
  HedgedPortfolioState.event_ordering (varsayılanlı alan, tip doğrulamalı)
  FUNDING_BEFORE_FILL: anahtar (time_us, faz FUNDING=0 < FILL=1, rate_type)
    kesin artan; imleç state'in kendi fills + applied_funding_keys kayıtlarından
    türetilir (çağıran veremez); last_event_time ile tutarsız geçmiş reddedilir.
  §19.10.1'in 22 testi DEĞİŞTİRİLMEDEN geçer.
  Bilinen sınır: dataclasses.replace ile elle kurulmuş keyfi state'e karşı tam
  koruma iddia edilmez (ledger'lar da böyle kurulabilir); mod yalnız
  constructor'da belirlenir, geçişler korur.

Replay — backtest/multileg_replay.py (commit 146775a, sertleştirme bu commit):
  HedgeAction {OPEN, CLOSE}; HedgeIntent(action, decision_time, quantity)
  LegCandles(dataset: CandleDataset, candles: tuple[Candle])
  run_multileg_replay(*, pair, spot, perpetual, funding_events, intents,
      spot_cash, perpetual_collateral, spot_cost_model, perpetual_cost_model,
      funding_model, as_of_time) -> MultiLegReplayResult
  MultiLegReplayResult: pair, spot/perpetual dataset, run_start, run_end,
      as_of_time, initial_state, final_state, paired_fills, funding_records
      (FundingRecord: event, lifecycle_before, pre_fill_perpetual_quantity,
      signed_cost, applied_to_open_position), equity_points (ReplayEquityPoint,
      pre_fill=True), trace (TraceEvent: time, kind, detail), unexecuted_intents,
      final_mark (run_end, son trade CLOSE), closed_summary (yalnız kapalıysa),
      position_open_at_end, intents_are_exogenous, valuation_source
      "trade_close", liquidation_not_modeled, legging_not_modeled,
      warmup_supported False
  Doğrulama: dataset kimliği pair'e karşı (spot_trade / contract_trade; index
    ve mark reddedilir), mum sembol/timeframe = dataset, ızgara eşitliği,
    funding kimliği pair perpetual'ı, duplicate ile çelişkili payload ayrı
    mesajla, tek bacaklı _validate_funding_events (aralık run_start <= t <
    run_end, (event_time, rate_type) sırası), intent script'i [] / [OPEN] /
    [OPEN, CLOSE], kesin artan, decision_time bir mum availability anı,
    eşit miktar. Döngü sonunda tüm funding olayları tüketilmiş olmalı.
  Sıra: her mum N için t = availability(N): funding(ler) -> pre-fill mark
    (iki CLOSE) -> intent -> N+1 OPEN'larında fill (zaman damgası t); son
    mumun intent'i UNEXECUTED. Funding cursor'ı tek geçiş.
  "Mark" = trade CLOSE değerlemesi; borsa markPrice serisi DEĞİL.
  Aritmetik çağıranın Decimal context'inde; araştırma çağrıları decimal_policy.

Kanıt (testler; beklenen değerler elle türetildi):
  N1–N7  tests/test_backtest_multileg_replay.py::test_n1..test_n7 (N7 iki varyant)
         N1 400/401/402/402 -> 402 · N2 402.0101 · N3 402.01515 ·
         N4 400/400.85/401.85/401.70, cüzdanlar 200.80 / 200.90 ·
         N5 401.7101 · N6 402 (funding sıfır kayıt) · N7 açık, 402, unrealized 1+1
  S1–S12 aynı dosya test_s1..test_s12
  E1     tests/test_backtest_multileg_ordering.py (8 test)
  D      tests/test_backtest_multileg_replay_adversarial.py (20 fonksiyon):
         bağımsız test-only ledger oracle, seed 20260925, 40 rastgele Decimal
         senaryo (her equity noktası birebir) + oracle'ın 3 enjekte kusuru
         (notional sayımı, funding işareti, çift ücret) yakalaması; nedensellik
         (sonraki HIGH/LOW/CLOSE fill'i ve önceki mark'ları değiştirmez,
         karar mumunun CLOSE'u fill fiyatı olmaz, sonek öneki değiştirmez);
         metamorfik (paralel fiyat kayması, maliyet/funding tam etkisi,
         kapanış sonrası fiyat bağımsızlığı); publication_lag kaydırması yok;
         düşmanca ortam context'inde açık context ile özdeş sonuç.
  E      tests/test_research_multileg_offline.py (4 test) + offline demo
Gözlemsel ölçek smoke'u (test değil): 8760 saatlik mum + 1095 funding tek
  çalıştırmada ~0.43 sn; final 2009.0494 elle doğrulandı (fiyat -2 +
  1094 x 0.0101). Not: E1 imleci her funding'de kayıtları tarar (O(k)),
  çok uzun serilerde karesel büyür; bu ölçekte sorun yok, iyileştirme
  gerekirse ayrı mikro-adım.
```

Sentetik offline demo (§19.12.8 kapsamı):

```
python -m crypto_quant_lab.research.multileg_offline --output <YENİ dizin>
research/multileg_offline.py — SENTETİK VERİ · SCRIPTED INTENT · STRATEJİ DEĞİL.
Senaryolar (sürüm multileg-offline/v1): closed_no_funding (402),
  close_instant_funding (402.0101), proportional_costs_and_funding (spot %0.1,
  perp %0.05 notional; 400 / 400.8490 / 401.8591 / 401.7076, cüzdanlar
  200.799 / 200.9086), open_at_end (402, HEDGED_OPEN). Beklentiler modül
  docstring'inde elle türetildi. Rapor v2 paketi (report.json + report.md):
  cüzdanlar, fill'ler, funding kayıtları, pre-fill equity zaman çizelgesi,
  açık/kapalı durum, trace, kapsam bayrakları, config + girdi manifesti,
  run_input_sha256. Ağ/store/var olan DB yok; var olan dizin ezilmez (exit 2);
  beklenti tutmazsa veya senaryo hata verirse rapor failed (exit 1).
  İki temiz dizinde aynı deterministic_sha256 ve run_input_sha256 doğrulandı.
```

Bağımsız son denetim (aynı oturumda ayrı tur, harici reviewer kullanılmadı):

```
Kontrol edilen ve testle bağlanan riskler: perp notional'ın nakde/equity'ye
  girmesi (oracle "count_notional" kusuru + A/N testleri), funding/realized/
  maliyetin çift sayımı (mark invariant'ı + oracle "double_fee"), kapanış
  anında funding'in atlanması (N2, S3), açılış anında funding'in yeni
  pozisyona uygulanması (N6, S4), aynı anlı farklı funding'lerin kaybolması
  (N3, S5), sıfır kaydın duplicate denetiminden kaçması (yeni regresyon testi),
  N+1 verisinin karara sızması (D4), pre-fill mark'ın post-fill sanılması
  (pre_fill bayrağı, N4 T3=401.85), açık pozisyona sahte kapanış (N7), ortam
  Decimal etkisi (D6), testlerin beklenenleri üretimden alması (elle türetilmiş
  sabitler + bağımsız oracle).
Bu turda yapılan düzeltmeler: (1) döngü sonunda tüm funding olaylarının
  tüketildiği savunmacı denetimi; (2) FLAT anındaki duplicate ve üç yaşam
  döngüsü fazındaki kayıtlar için regresyon testi. Ekonomik hata bulunmadı;
  bu "hata yoktur" demek değildir — yalnız listelenen senaryolar ve
  invariant'lar doğrulandı.
Açık kalan: E1'in replace ile elle kurulan state'e karşı sınırı; O(k)
  imleç maliyeti; çok bacaklı store-backed runner, partial fill/legging,
  margin/liquidation, warmup, metrics adapter'ı.
```

Durumlar: Multi-leg accounting first slice IMPLEMENTED + TESTED · E1 ordered-events extension IMPLEMENTED + TESTED · Legacy accounting compatibility VERIFIED · In-memory multi-leg replay IMPLEMENTED + TESTED · Synthetic offline demo VERIFIED · Store-backed multi-leg runner NOT IMPLEMENTED · Partial fill/legging PENDING · Margin/liquidation NOT MODELED · Basis/carry strategy evaluation PENDING · Faz 7 NOT COMPLETE · FAZ6C NOT COMPLETE · FAZ6D NOT STARTED.

### 19.13 Spot provenance + salt okunur store-backed çok bacaklı replay — IMPLEMENTED + TESTED (2026-09-25)

Yalnız bu dilim uygulanmış ve SENTETİK store'larla test edilmiştir; gerçek piyasa verisiyle KOŞULMADI. Strateji, basis/carry değerlendirmesi veya kârlılık kanıtı değildir; intent'ler scripted'dır. §19 genel olarak DRAFT kalır.

#### 19.13.1 Başlangıç bulgusu (koddan)

```
data_quality/ingestion.py::ingest_binance_historical_range spot mumlarını yalnız
store.write_batch ile yazar: candle_datasets provenance'ı ve candle_coverage
YAZILMAZ; boş yanıtta hiçbir şey yazılmaz (erken return). Eksik olan: metadata
yazımı + coverage kanıtı. Okuyucu tarafı (query_dataset / query_coverage) ve
atomik yazım yolu (write_ingestion_batch) §10'dan beri vardı.
Funding store şemasında (historical_funding_events / _coverage) KAYNAK ALANI
YOKTUR: yalnız partition (exchange, market_type, symbol) + coverage.
```

#### 19.13.2 Kanıt tablosu

```
Veri      Gerekli kanıt              Mevcut temsil                  Doğrulama                     Ret nedeni
spot      kimlik + kaynak etiketi    candle_datasets (price_kind    registered == beklenen        missing_provenance /
          + tam pencere coverage     spot_trade, source)            (kaynak etiketi dahil)        provenance_mismatch
          + her grid mumu            candle_coverage                coverage_contains [s, e)      incomplete_coverage
                                     historical_candles             satır sayısı == slot sayısı   missing_data
perp      aynı (contract_trade)      aynı tablolar                  aynı                          aynı
funding   partition + tam pencere    historical_funding_coverage    kalite raporu PASS [s, e)     incomplete_coverage
          coverage; olay değerleri   historical_funding_events      olaylar kanonik sırada;       (replay: duplicate/
                                     (KAYNAK YOK)                   replay doğrulaması            sıra/aralık hataları)
Kimlik ≠ içerik fingerprint'i ≠ coverage ≠ kaynak erişim kanıtı. Bugün hesaplanan
satır hash'i geçmişte hangi endpoint'ten alındığını kanıtlamaz; ham yanıt arşivi
veya fetched_at alanı YOKTUR ve uydurulmaz. Kaynak etiketi, yazan ingestion
yolunun beyanıdır.
Doğrulanmış boş funding coverage'ı = gerçek sıfır funding penceresi; coverage
yoksa (toplanmamış) ret — asla sıfır funding sayılmaz.
```

#### 19.13.3 Spot provenance eklemesi (additive)

```
storage/datasets.py: BINANCE_SPOT_KLINES_SOURCE =
  "binance:GET https://data-api.binance.vision/api/v3/klines" (mevcut spot
  adaptörünün gerçekten çağırdığı uç nokta); binance_spot_trade_dataset(symbol, tf)
data_quality/ingestion.py: ingest_binance_spot_klines_with_provenance(store, *,
  symbol, timeframe, requested_start, requested_end, as_of_time,
  fetch_page=None, source=None, max_attempts=3) -> UsdmKlineIngestionResult
  - mevcut atomik yol (usdm_ingestion._ingest) yeniden kullanılır: tüm sayfalar
    alınıp doğrulanmadan hiçbir şey yazılmaz; mumlar + provenance + coverage TEK
    write_ingestion_batch transaction'ında
  - gerçek adaptörde source sabittir (verilemez); değiştirilmiş transport
    (fetch_page) kendi source etiketini beyan ETMEK ZORUNDADIR — sentetik veri
    Binance diye etiketlenemez
  - boş ama tam aralık: provenance + coverage, sıfır mum (yetkili yokluk)
  - aynı içerikle tekrar idempotent; çelişkili içerik / farklı kaynak
    DataConflictError; provenance'sız eski satırlar asla yeniden etiketlenmez;
    kayıtlı namespace'e eski write_batch yolu StorageError
  - ingest_binance_historical_range DEĞİŞMEDİ (hâlâ provenance'sız).
Şema/migration değişikliği yok.
```

#### 19.13.4 Salt okunur store-backed runner

```
research/multileg_store.py
  CandleStoreSource(path, expected_source)
  StoreBackedMultiLegRequest(pair, timeframe, run_start, run_end, as_of_time,
      spot, perpetual, funding_path, intents, spot_cash, perpetual_collateral,
      spot_cost_model, perpetual_cost_model, funding_model, decimal_context)
  default_request(**fields)  # sıfır maliyet, LinearFundingModel, belgelenmiş Decimal varsayılanı
  run_store_backed_multileg_replay(request) -> StoreBackedMultiLegRun(request,
      spot, perpetual: CandleEvidence, funding: FundingEvidence,
      replay_input_sha256, result: MultiLegReplayResult, snapshot_scope)
  StoreInputError(reason ∈ {invalid_request, missing_provenance,
      provenance_mismatch, incomplete_coverage, missing_data})
  describe_cost_model / describe_funding_model
Akış: istek doğrulama -> her store open_read_only (dosya/tablo/migration
  yaratılmaz) -> her store kendi read_snapshot'ı içinde provenance + coverage +
  satırlar (store başına tek sorgu turu) -> kimlik/coverage/grid/funding
  denetimi -> LegCandles + HistoricalFundingEvent -> explicit Decimal context
  altında değişmemiş run_multileg_replay. Herhangi bir denetim başarısızsa
  replay ÇAĞRILMAZ. Kaynak store'lara hiçbir şey yazılmaz.
Snapshot kapsamı: her store'un satırları, provenance'ı ve coverage'ı tek commit
  durumundan gelir; store'lar sırayla okunur — store'lar arası atomik snapshot
  YOKTUR (sonuçta snapshot_scope alanı).
Fingerprint'ler: replay_input_sha256 = tüketilen mum/funding değerleri, pencere,
  as_of, intent'ler, cüzdanlar, maliyet/funding model parametreleri, Decimal
  context; parametreleri tanımlanamayan model varsa None (yalnız sınıf adıyla
  tekrar-üretilebilirlik iddia edilmez). store_provenance_sha256 = kimlik +
  coverage (ayrı alan). Sorgulanmayan satır replay girdisini değiştirmez.
```

#### 19.13.5 Kabul kanıtı (tests/test_research_multileg_store.py, 21 öğe; tests/test_spot_provenance_ingestion.py, 8)

```
ST1  store yolu == aynı girdilerle in-memory yol (5 konfigürasyon; tüm sonuç nesnesi)
ST2  N-fixture değerleri kabul dosyasından import edilerek store yolundan:
     N1 400/401/402/402 (201 + 201) · N2 402.0101 · N3 402.01515 · N5 401.7101 ·
     N7 açık, realized 0 + 0, unrealized 1 + 1, 402
ST3  oransal (0.001 / 0.0005) 401.7076 ile sabit (0.10 / 0.05) 401.7101 ayrı;
     sabit test modeli için replay_input_sha256 None
ST4  provenance'sız spot, yanlış kaynak, mark_price namespace'i, kısmi coverage,
     yarım/başarısız ingestion -> belirli ret; store'lar değişmez, etiketlenmez
ST5  coverage içinde eksik mum (missing_data), 4h isteği, hizasız pencere,
     as_of < run_end; +03:00 eşdeğer anlar aynı sonuç
ST6  doğrulanmış boş funding = sıfır; toplanmamış / yarım / başka pair coverage'ı
     -> incomplete_coverage; FLAT ve FLAT_CLOSED sıfır kayıtları store'dan
ST7  olmayan yol yaratılmaz; başarı ve hata sonrası kaynak dosyalar bayt+mtime
     aynı, -wal/-shm/-journal yok
ST8  WAL modunda okuma sırasında deterministik biçimde commit edilen eşzamanlı
     güncelleme snapshot içinde görünmez (sonra görünür); hata sonrası
     bağlantılar serbest (Windows'ta dosya taşınabildi); store başına tek sorgu;
     snapshot_scope "no atomic snapshot across stores"
ST9  aynı girdi aynı fingerprint; intent/maliyet/cüzdan/Decimal/funding store/fiyat
     değişimi fingerprint'i değiştirir; pencere dışı satır değiştirmez
ST10 düşmanca ortam context'inde aynı sonuç ve fingerprint; global context
     değişmez; ağ çağrıları yasaklıyken çalışır
ST11 geçersiz store girdisinde replay spy'ı hiç çağrılmaz (intent script'i
     doğrulaması replay'in kendi etkisiz ön denetimidir)
ST12 tam suite 2620 passed; §19.10.1'in 22 testi ve gece testleri değişmedi
Demo: iki temiz dizinde aynı deterministic_sha256; doğrulanamayan kaynakla failed paket.
```

#### 19.13.6 Offline store demosu

`python -m crypto_quant_lab.research.multileg_store_demo --output <YENİ dizin>` — `<çıktı>\fixture\` altında gerçek yazıcılarla sentetik store'lar (spot: provenance'lı spot ingestion + sahte transport; perpetual: candle store `write_ingestion_batch`, çünkü USDⓈ-M ingestion fonksiyonu her zaman Binance uç noktasını yazar [2026-09-25 notu: §19.14 ile `source` parametresi eklendi; demo değiştirilmedi]; funding: funding store `write_ingestion_batch`), yazıcılar kapatıldıktan sonra salt okunur runner ile dört senaryo, rapor v2. Eski `multileg_offline` komutu değişmedi ve çalışıyor.

#### 19.13.7 Denetim (ayrı tur, harici reviewer yok)

```
Aranan ve testle bağlanan riskler: kaynak kanıtı uydurma (source etiketi yazan
yolun beyanı; sentetik transport Binance etiketi alamaz), eksik coverage'ı
tam sayma (coverage_contains + slot sayısı), boş funding ile toplanmamış
funding'i karıştırma (ST6), okuma sırasında DB yazımı (ST7, read-only
bağlantı), yanlış snapshot iddiası (per-store kapsam, ST8), yanlış price_kind
(ST4), N+1 sızıntısı (replay değişmedi; gece D4 testleri), funding/ücret çift
sayımı (ST1 parity + ST2/ST3 elle değerler), eksik fingerprint (ST9), açık
pozisyonun sahte kapanışı (ST2 N7), yalnız aynı kodun iki kez karşılaştırılması
(ST2/ST3 elle türetilmiş sabitlerle desteklendi).
Açık bulgular (bu dilimde değiştirilmedi):
- research/offline_fixture.py (gece paketi) sentetik perpetual/index verisini
  USDⓈ-M ingestion ile yazdığı için Binance kaynak etiketi taşır; bu yeni runner
  onu "Binance" diye doğrulamaz ama etiket yanıltıcıdır. Düzeltme ayrı karar.
  [2026-09-25 notu: §19.14'te düzeltildi.]
- USDⓈ-M ingestion fonksiyonları sentetik kaynak etiketi kabul etmez (spot'taki
  source parametresinin eşdeğeri yok). [2026-09-25 notu: §19.14'te eklendi.]
- Funding kaynağı şemada yok; eklenmesi şema/migration kararıdır.
- Mevcut spot HTTP adaptörü HTTPError'ı da ConnectionError'a çevirir (retry
  edilir); bu dilimde değiştirilmedi.
```

Durumlar: Multi-leg accounting first slice IMPLEMENTED + TESTED · E1 IMPLEMENTED + TESTED · In-memory multi-leg replay IMPLEMENTED + TESTED · Synthetic offline demo VERIFIED · Spot provenance ingestion IMPLEMENTED + TESTED · Read-only store-backed multi-leg replay IMPLEMENTED + TESTED (yalnız sentetik store'larla) · Real-market store-backed run NOT PERFORMED · Partial fill/legging PENDING · Margin/liquidation NOT MODELED · Basis/carry strategy evaluation PENDING · Faz 7 NOT COMPLETE · FAZ6C NOT COMPLETE · FAZ6D NOT STARTED.

### 19.14 USDⓈ-M kaynak etiketi desteği + sentetik fixture kaynak düzeltmesi — IMPLEMENTED + TESTED (2026-09-25)

Yalnız kaynak METADATA'sı değişti; fiyat, funding, maliyet, zamanlama, muhasebe veya strateji değişmedi. Gerçek piyasa verisiyle koşulmadı. §19 genel olarak DRAFT kalır.

#### 19.14.1 Başlangıç bulgusu (koddan)

```
research/offline_fixture.py sentetik contract/index mumlarını
ingest_binance_usdm_perpetual_klines / ingest_binance_usdm_index_price_klines
ile sahte fetch_page üzerinden yazıyordu; bu fonksiyonların source parametresi
yoktu, dataset her zaman kanonik Binance uç noktasıyla etiketleniyordu
("binance:GET https://fapi.binance.com/fapi/v1/klines" /
 ".../fapi/v1/indexPriceKlines"). Okuyucular (cli inspect/doctor/basis-report/
funding-research, research/basis.py, research/usdm_perpetual.py) kanonik
kaynağı birebir eşitlikle beklediği için etiket yanıltıcı olduğu hâlde
fixture geçiyordu. research/multileg_offline.py'deki in-memory sentetik
perpetual dataset'i de Binance etiketi taşıyordu.
```

#### 19.14.2 API (additive, geriye uyumlu)

```
storage/datasets.py
  binance_usdm_perpetual_contract_trade_dataset(symbol, tf, *, source=BINANCE_USDM_KLINES_SOURCE)
  binance_usdm_index_price_dataset(pair, tf, *, source=BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE)
data_quality/usdm_ingestion.py
  ingest_binance_usdm_perpetual_klines(..., source: str | None = None)
  ingest_binance_usdm_index_price_klines(..., source: str | None = None)
  kural (_source_for):
    fetch_page yok (gerçek adaptör) + source yok  -> kanonik uç nokta (eski davranış)
    fetch_page yok + source verildi               -> ValueError, istek/yazım YOK
    fetch_page var + source verildi               -> beyan edilen etiket, olduğu gibi
    fetch_page var + source yok                   -> LEGACY: kanonik etiket (aşağı bkz.)
  boş / yalnız boşluk -> ValueError("source cannot be empty"), str olmayan ->
  TypeError; ikisi de CandleDataset kurulurken, ilk fetch'ten ÖNCE.
research/basis.py  load_close_basis_history(..., contract_source=kanonik, index_source=kanonik)
research/usdm_perpetual.py  evaluate_usdm_perpetual_funding_research(..., contract_source=kanonik)
research/cli.py  config v1'e opsiyonel "sources": {"contract"?: str, "index"?: str}
  - verilmeyen rol kanonik uç noktadır (eski config'ler aynen çalışır)
  - bilinmeyen rol, boş/boşluk/str olmayan değer, nesne olmayan alan -> ConfigError
  - inspect, doctor, basis-report, funding-research beklenen dataset'i
    ResearchConfig.expected_dataset(role) ile kurar; eşitlik kuralı GEVŞETİLMEDİ
    (kaynak dahil birebir). effective() değişmedi: config'de sources yoksa
    kaydedilen config ve config_sha256 öncekiyle aynı.
Şema/migration, rapor şeması (v2), pyproject/bağımlılık değişikliği yok.
```

LEGACY sınırlaması (bilinçli olarak korundu, testle sabitlendi): enjekte edilmiş transport ile `source` vermeden çağrılan USDⓈ-M ingestion, veri o uç noktadan gelmediği hâlde kanonik Binance etiketini yazar. Mevcut çağıranları kırmamak için korunmuştur; yeni sentetik yazıcılar `source` vermelidir. Düzeltilmiş fixture bu yolu kullanmaz. (Spot ingestion'da §19.13.3 bu durumda reddeder; USDⓈ-M'de ret, mevcut testleri/çağıranları kıracağından ayrı karardır.)

#### 19.14.3 Sentetik kimlikler

```
research/offline_fixture.py
  FIXTURE_CONTRACT_SOURCE = "synthetic:offline-fixture/contract-trade/v1"
  FIXTURE_INDEX_SOURCE    = "synthetic:offline-fixture/index-price/v1"
  her iki ingestion çağrısı source ile; fixture_config()["sources"] aynı etiketler
research/multileg_offline.py
  _PERP_DS.source = "synthetic:perpetual" (aynı modüldeki "synthetic:spot" ile tutarlı)
Kimlik alanları (exchange="binance", market_type, symbol, timeframe, price_kind)
DEĞİŞMEDİ; yeni piyasa kimliği uydurulmadı. Mevcut DB'ler SQL UPDATE ile
yeniden etiketlenmedi; yeni fixture'lar yeni dizinlerde üretilir.
```

#### 19.14.4 Önce/sonra karşılaştırması (alan düzeyinde, rapor v2)

```
Rapor              status     deterministic_sha256  config_sha256     run_input_sha256
offline-smoke  önce succeeded 94d2710b9e34ce66     7de15e518239fe4c  8d8b890c5d6070a3
               sonra succeeded 641201f98b02cba0    955cdb55a533c08d  380c13630772f8ec
multileg-off.  önce succeeded fccfa2f14112a4af     9235ca477dd675a8  4c6a4380f6384ed6
               sonra succeeded f1bc8edeb580a695    9235ca477dd675a8  7df372d2320b8393
multileg-store önce/sonra      d635c25f1a179540     d5aaf08941e1ecb6  45b91cea8fedd158 (değişmedi)
Sonra değerleri üç ayrı temiz dizinde aynı.
Grup A (ekonomi, AYNI): tüm results, check durumları, equity/fill/funding/maliyet
  değerleri; fixture store'larındaki historical_candles (24 contract, 23 index
  satırı) ve candle_coverage satırları bayt bayt aynı. Kabul değerleri:
  N1 402 · N2 402.0101 · N3 402.01515 · N5 401.7101 · N7 açık 402 · oransal
  maliyet demosu 401.7076.
Grup B (kasıtlı değişen): offline-smoke config.sources (yeni alan),
  config_sha256, inputs[0..1].registered_dataset.source,
  inputs[0..1].logical_fingerprint_sha256, contract/index provenance check
  detay metinleri, run_input_sha256, deterministic_sha256; multileg-offline
  inputs[1].dataset kaynak alanı, run_input_sha256, deterministic_sha256.
Grup C (uçucu): run_metadata.created_at, run_metadata.git_tracked_changes.
Kaynak fingerprint'ten ÇIKARILMADI; rapor şeması yükseltilmedi; eski raporlar
yeniden yazılmadı. sources içermeyen bir config'in config_sha256'sı önceki
değere eşittir (P9 testi 7de15e51...'i sabitler).
```

#### 19.14.5 Kabul kanıtı (tests/test_usdm_source_labels.py, 51 öğe)

```
P1  varsayılan gerçek adaptör (patch'li urlopen): fapi URL'leri, kanonik etiketler
P2  gerçek ingestion üzerinden beyan edilen sentetik etiket (contract + index),
    tam provenance + coverage; builder varsayılanları kanonik
P3  "", "   ", "\t\n", 123, b"..." -> belirli TypeError/ValueError; spy fetch
    hiç çağrılmaz, store boş; gerçek adaptör + source -> ret, urlopen spy boş
P4  LEGACY: source'suz enjekte transport kanonik etiket yazar (sınırlama olarak
    sabitlendi); düzeltilmiş fixture store/config sentetik etiket taşır;
    multileg_offline manifest'inde Binance etiketi yok
P5  yarım/sırasız sayfalama hiçbir şey yazmaz; boş tam aralık = etiketli yetkili
    yokluk; aynı içerik idempotent; farklı etiket / legacy etiket / farklı
    içerik DataConflictError; kanonik store sentetik etiketle yeniden
    etiketlenmez; provenance'sız satırlar etiketlenmez
P6  eşleşen config geçer; sentetik store + kanonik config ve kanonik store +
    sentetik config doctor/inspect/basis-report/funding-research'te failed;
    tek rol farkı yalnız o rolü düşürür; okuyucular boşluk/büyük harf farkını
    da reddeder; geçersiz sources config'i (8 biçim) ConfigError
P7  sentetik ve kanonik etiketli aynı satırlar: mum/coverage satırları ve
    funding-research sonuçları/check durumları aynı
P8  multileg_offline dört senaryo: etiket değişimi sonuç görünümünü değiştirmez;
    402 / 402.0101 / 401.7076 / 402
P9  config'siz-sources digest sabit; offline-smoke iki koşuda aynı
    deterministic/run_input hash; etiket input fingerprint'lerine girer
P10 ağ yasakken offline-smoke çalışır; var olan çıktı ezilmez (exit 2, bayt
    aynı); global Decimal context değişmez
P11 tam suite 2671 passed (önce 2620; +51 yeni test); tek mevcut test
    değişikliği: tests/test_research_cli.py::_trial yardımcısı fixture'ın beyan
    ettiği contract kaynağını geçer (gevşetme değil, beklenen değer güncellemesi)
```

#### 19.14.6 Denetim (ayrı tur, harici reviewer yok)

```
Kontrol edilen riskler: okuyucu eşitliğinin gevşemesi (yok; P6 boşluk/büyük harf
farkını da reddeder), eski config digest'inin kayması (P9 sabit hash),
ekonominin değişmesi (grup A + P7/P8), gerçek adaptöre sahte etiket (P3),
fetch sonrası doğrulama (P3 spy), mevcut store'un yeniden etiketlenmesi (P5),
ağ/ezme/context sızıntısı (P10), korumalı dosyalar (backtest/*, report.py,
test_backtest_multileg.py, pyproject.toml değişmedi; AGENTS.md hash aynı).
Denetimde düzeltilen: ResearchConfig.expected_dataset bilinmeyen rolü sessizce
index sayıyordu -> ValueError (testli); offline_fixture ve multileg_store_demo
docstring'lerindeki eskimiş kaynak ifadeleri güncellendi (davranış değişmedi).
Açık sınırlar (bu dilimde çözülmedi): funding store'da kaynak kolonu yok;
parametreleri tanımlanamayan maliyet modelinde replay fingerprint'i None;
spot HTTP adaptöründe HTTPError retry sınıflandırması; E1 performansı;
source'suz enjekte USDⓈ-M transport'unun legacy kanonik etiketi.
```

Durumlar: USD-M source support IMPLEMENTED + TESTED · Synthetic offline fixture source fix IMPLEMENTED + TESTED · Real-market run NOT PERFORMED · Faz 7 NOT COMPLETE · FAZ6C NOT COMPLETE · FAZ6D NOT STARTED.

### 19.15 Config tabanlı çok bacaklı araştırma çalıştırıcısı — IMPLEMENTED + TESTED (2026-09-25; yalnız sentetik store'larla)

Kapsam: config → mevcut salt okunur store runner (§19.13.4) → değişmemiş `run_multileg_replay` → rapor v2. Muhasebe, E1, replay, store runner, store şeması, HTTP adaptörü, retry ve USDⓈ-M legacy davranışı DEĞİŞMEDİ. Gerçek piyasa verisiyle koşulmadı; strateji/sinyal yoktur.

#### 19.15.1 Giriş noktaları (research CLI'ya additive)

```
python -m crypto_quant_lab.research multileg-replay --config <dosya> --output <YENİ dizin>
python -m crypto_quant_lab.research multileg-example --output <YENİ dizin>
research/multileg_config.py
  load_multileg_config(path) -> MultilegConfig(raw, sha256, stores, decimal_context, request)
  parse_multileg_config(raw, base_dir) ; multileg_section(config) -> Section
  multileg_replay_builder(path) ; example_config() ; write_example(directory)
Rapor: mevcut run_to_bundle (report v2 zarfı, OutputBundle, _sanitize). Exit
kodları mevcut sözleşme: 0 succeeded (warning sayılır), 1 failed, 2 çıktı
dizini var/üst dizin yok. Yeni exit-code ailesi yok.
```

#### 19.15.2 Config şeması ve parser kuralları

Şema: docs/RESEARCH_RUNBOOK.md §6d. Hiçbir ekonomik alan varsayılanla doldurulmaz (pair, pencere, store'lar, kaynak etiketleri, cüzdanlar, maliyet modelleri, funding modeli, intent script'i zorunlu); yalnız `decimal_context` belgelenmiş varsayılana düşer ve rapora çözülmüş yazılır. Hedge oranı yapılandırılamaz (1:1, §19.10.1 sınırı). Ret (ConfigError, alan adıyla, herhangi bir store açılmadan): bilinmeyen alan/sürüm/tür, tekrarlanan anahtar, kesirli/üslü JSON sayısı, NaN/Infinity, null/boolean, geçersiz ondalık/zaman, hizasız pencere, `as_of < run_end`, eksik `sources`, `sources.funding`, desteklenmeyen maliyet/funding modeli ve parametresi, iç içe composite, `warmup`, desteklenmeyen intent script'i (çakışan/ters sıralı/ikinci lifecycle/kısmi kapanış/ızgara dışı/pencere dışı zaman, ≤ 0 miktar), aynı dosyayı gösteren roller (yol veya hard link). Store yolları config dosyasına göredir (cwd'ye değil). Test-only `FixedCost` production'a taşınmadı.

#### 19.15.3 Sonuç ve durum sözleşmesi

```
results: result_view (§19.12 demo görünümü: final wallets, realized/unrealized,
  costs, funding_paid, final_equity, fills, funding_records, pre-fill equity
  zaman çizelgesi, unexecuted_intents, trace, scope) + initial_wallets,
  intent_count, paired_fill_count, no_trade_explanation, identity_scope
checks: inputs.provenance_and_coverage (passed; aksi hâlde runner ret eder),
  identity.replay_input (fingerprint yoksa FAILED), intents.execution
  (gerçekleşmeyen intent -> WARNING), position.at_end (açık pozisyon -> WARNING)
Boş intent listesi: FLAT, 0 işlem, succeeded, açıklama "scripts no intents ...
  not a data error". Son mum intent'i: unexecuted + warning, dolmuş gibi
  gösterilmez. Veri/provenance/coverage hatası: failed, results null, hata
  StoreInputError nedeniyle ([provenance_mismatch] vb.). Accounting reddi
  (ör. yetersiz nakit): failed, "ValueError: insufficient spot cash ...".
```

#### 19.15.4 Kimlikler

```
config_sha256       config dosyası yazıldığı gibi (kanonik JSON); koşu kimliği DEĞİL
                    [2026-09-25 notu: teknik olarak ayrıştırılmış config'in kanonik
                    JSON hash'idir, ham bayt hash'i değil; §19.16.2]
replay_input_sha256 runner tanımı (§19.13.4): tüketilen mum/funding değerleri,
                    pencere, as_of, intent'ler, cüzdanlar, maliyet/funding
                    parametreleri, Decimal context; kaynak etiketleri HARİÇ
run_input_sha256    etkin config (store dosya adları, beyan edilen kaynaklar,
                    çözülmüş Decimal context) + her girdinin mantıksal
                    fingerprint'i (spot, perpetual, funding, replay_input)
deterministic_sha256 raporun deterministik ÇIKTI yükü
Çıktı yolu, cwd ve oluşturma saati bunların hiçbirini değiştirmez (saat ve git
durumu run_metadata'da). Tanımlanamayan model: programatik yol None'ı korur;
config yolu bilinmeyen modeli reddeder; runner None dönerse veya fingerprint
hata verirse koşu FAILED olur (yanlış tekrar-üretilebilir başarı yok).
```

#### 19.15.5 Kabul kanıtı (tests/test_research_multileg_config.py, 97 öğe)

```
C1  multileg-example -> multileg-replay: runner, iki candle open_read_only
    (spot.db, perpetual.db), bir funding open_read_only ve replay tam birer kez
    çağrılır; kontrollü config değişikliği (CLOSE yok -> 401.8591; perpetual
    maliyeti zero -> 401.8091) ve store içeriği değişikliği (son spot close
    103 -> 404) sonucu elle türetilmiş değere taşır
C2  7 konfigürasyonda config yolu == bağımsız kurulan programatik istek:
    MultiLegReplayResult'ın tamamı (fills, funding, equity, final state,
    unexecuted, trace), replay_input_sha256 ve rapor results görünümü
C3  config yolunda: N1 402 · N2 402.0101 · N3 402.01515 · N7 açık 402 ·
    oransal 401.7076 (equity dizileriyle). N5 401.7101 (FixedCost) yalnız
    programatik yolda (ST2/ST3); config'te "fixed" reddedilir
C4  34 alan ihlali + 7 JSON düzeyi ihlali belirli mesajla; erken retlerde
    runner ve open_read_only spy'ları hiç çağrılmaz; eksik config dosyası failed
C5  yanlış/boşluklu kaynak, index_price namespace, provenance'sız spot,
    kapsam dışı pencere, eksik mum, toplanmamış/yarım/başka partition funding
    -> belirli StoreInputError; doğrulanmış boş funding = sıfır; raporda
    yerel yol yok
C6  15 intent ihlali reddi; boş liste FLAT/0 işlem/açıklama; son mum intent'i
    unexecuted + warning; CLOSE son mumda -> açık 402, unrealized 1 + 1;
    accounting reddi failed
C7  farklı cwd + göreli config yolu ve farklı çıktı yolu aynı deterministic ve
    run_input; taşınmış fixture aynı; gerçekten değişen kaynak etiketi
    ekonomiyi değil run_input/deterministic'i değiştirir
C8  funding değeri, intent zamanı, miktar, cüzdan, maliyet parametresi, as_of,
    Decimal context, tüketilen fiyat -> replay_input ve run_input değişir;
    kaynak etiketi -> yalnız run_input; pencere dışı satır ve çıktı yolu ->
    tüketilen kimlikler aynı
C9  programatik bilinmeyen model None; describe None enjeksiyonu -> failed;
    fingerprint hatası -> failed
C10 başarı/hata sonrası store'lar + config bayt/mtime aynı, -wal/-shm yok,
    bağlantılar serbest; eksik store oluşturulmaz; çıktı ezilmez (exit 2);
    yol ve hard-link alias'ı reddedilir
C11 ağ yasakken çalışır; düşmanca ortam context'inde aynı deterministic;
    hata dahil global context değişmez; import yan etkisiz
C12 mevcut komutlar kayıtlı; tam suite 2768 passed (öncesi 2671)
Offline kanıt: iki temiz çıktı dizini (ve ayrı bir örnek fixture dizini) aynı
deterministic 2f8dbf4f…, config 986af995…, run_input b8b11c30…,
replay_input 6448d759…; açık-final varyantı 401.8591, 1 warning.
```

#### 19.15.6 Elle türetilmiş örnek değerleri

```
Örnek (oransal 0.001 / 0.0005, funding 0.0001 @T3, ref 101):
OPEN @T1 dolum spot 100 / perp 102; maliyet 0.1 + 0.051
T2: 99.9 + 101 + 199.949 + 0 = 400.849
T3: funding short'a +0.0101; perp PnL +1 -> 401.8591
CLOSE @T3 dolum 101 / 101; maliyet 0.101 + 0.0505 -> 401.7076
CLOSE kaldırılırsa: 401.8591 (kapanış maliyeti yok, son close 101/101)
Perpetual maliyeti zero: 402.0101 - 0.1 - 0.101 = 401.8091
```

#### 19.15.7 Denetim (ayrı tur, harici reviewer yok)

```
Parser: ekonomik varsayılan uydurmuyor, girdi düşürmüyor (bilinmeyen alan
reddi); kaynak/funding provenance için yalnız beyan + coverage iddiası
(limitations); config hash'i koşu kimliği diye sunulmuyor (identity_scope);
bilinmeyen model/None başarıya dönmüyor (C9); açık pozisyon ve pre-fill mark
result_view'den (C6); eski config/API/hash'ler değişmedi (cli.py yalnız
additive; research config v1 kuralları aynı); alias/yol ile girdi yazımı yok
(C10); testler aynı kodu iki kez karşılaştırmakla yetinmiyor (C1/C3 elle
değerler). Denetimde eklenen: accounting reddinin veri hatasından ayrı
raporlandığını bağlayan test.
Açık sınırlar: funding store'da kaynak kolonu yok; store'lar arası atomik
snapshot yok; hedge oranı 1:1; mark-price serisi, liquidation, legging,
partial fill, borrow, lot/tick, wallet transfer modellenmez; config'te
warmup yok; multileg config için ayrı `doctor` yok; example dizini yarıda
kalan bir yazımda kısmi kalabilir (yeni dizin; kaynak store'lara dokunmaz).
```

Durumlar: Config-driven multi-leg research runner IMPLEMENTED + TESTED (yalnız sentetik store'larla) · Real-market run NOT PERFORMED · Multi-leg strategy NOT IMPLEMENTED · Faz 7 NOT COMPLETE · FAZ6C NOT COMPLETE · FAZ6D NOT STARTED.

### 19.16 Tam talimat uzlaştırması + isteğe bağlı `multileg-doctor` — IMPLEMENTED + TESTED (2026-09-25; yalnız sentetik store'larla)

Metin teslimi: §19.15 yazılırken görev metninin 1–8. bölümleri ulaşmamıştı (A–F paketleri o zaman kendimce tanımlanmıştı). Bu bölümde görevin TAM metni (EK A, 12 bölüm) koda karşı eşlendi; eksik talimat bug kanıtı sayılmadı. Kaynak görev dosyası kullanıcıya aittir, depoya eklenmedi.

#### 19.16.1 EK A uzlaştırma tablosu (gruplu)

```
Gereksinim                                  Kod                                   Test / kanıt                                  Durum
§4 açık ekonomik alanlar, sürüm, pair       multileg_config.parse_multileg_config  C4 (41 ret), C3/C1 değerleri                  VERIFIED
§4 sıkı JSON: duplicate/tip/float/bool/null  _no_duplicate_keys, _object, _string,  C4 JSON ve alan retleri; R1 boş ondalık ""   VERIFIED (boş ondalık testi bu turda eklendi)
   /NaN/Decimal/timestamp                   cli._decimal/_time
§4 allowlist modeller; bileşen sırası        _cost_model (_COST_FIELDS)            C3 "fixed" ret; R1 composite/spread/slippage  VERIFIED (composite/spread/slippage config
                                                                                   401.6583 / 401.8985, ters sıra ayrı kimlik     yolu önceden TESTSİZDİ)
§4 enstrüman "market" alanı                  pair: exchange + iki sembol + quote;  registered_dataset.market_type raporda        IMPLEMENTED — market_type HedgedPair
                                            market_type sabit (spot/usdm_perp.)                                                 sözleşmesiyle sabit, config alanı değil
§4 price_kind + source                       runner price_kind'ı sabitler; source  C5 source/price_kind retleri                   VERIFIED
                                            config'te zorunlu
§5 ekonomi/replay kuralları (tek pair, 1:1,  değişmemiş run_multileg_replay        C2 tam sonuç parity'si; acceptance N1–N7;      VERIFIED
   iki cüzdan, tek lifecycle, MS9/E1,                                             C6 son mum / açık pozisyon
   son açık pozisyon, unexecuted)
§6 kimlik: bilinmeyen model reddi, legacy    _cost_model; multileg_store None      C9, R12                                       VERIFIED
   None korunur, eksik kimlik başarı değil
§6 kimliğin provenance/coverage kapsaması    ÖNCE: run_input_sha256 yalnız config  R1 multileg_input testi                        MISSING -> DÜZELTİLDİ (19.16.2)
                                            + tüketilen içerik fingerprint'i
§6/§8 config_sha256 açıklaması               sha256(canonical_json(parsed config)) R1 config_sha256 testi                          CONFLICT (metin) -> DÜZELTİLDİ; hash aynı
§7 config-relative yol, salt okunur, eksik   parse (base_dir), open_read_only,     C7, C10, R7                                   VERIFIED
   DB yaratılmaz, bağlantılar kapanır        _require_store_files
§7 output config/input/alias üzerine yazmaz  OutputBundle (var olan yol = exit 2)  R1 output çakışma testi (config, store,       VERIFIED (test bu turda eklendi)
                                                                                   hard-link alias, fixture dizini)
§7 üç rol ayrı dosya                         YALNIZ config parser tercihi          C10 alias testleri                             IMPLEMENTED — programatik runner paylaşılan
                                                                                                                                dosyayı reddetmez; eski API zorunluluğu değil
§8 JSON+Markdown: input kaynak/partition/    ÖNCE: report.md config ve inputs'u    R1 markdown testi                              MISSING -> DÜZELTİLDİ (results'a
   coverage, model parametreleri, Decimal    göstermiyordu (render_markdown yalnız                                                  resolved_config + input_evidence)
   context                                   results'u basar)
§8 no-trade / unexecuted / accounting /      multileg_section checks               C6, C5, accounting testi                       VERIFIED
   veri hatası / warning ayrımı
§9 C1–C12                                    tests/test_research_multileg_config   97 öğe                                         VERIFIED
§10 çalışan örnek, iki temiz dizin           multileg-example, runbook §6d         §19.15.5 kanıtı; 19.16.5                       VERIFIED
§11 kapsam dışı maddeler                     —                                     dokunulmadı                                    OUT_OF_SCOPE
§12 denetim/checkpoint/commit                —                                     §19.15.7, 19.16.6                              VERIFIED
```

#### 19.16.2 B düzeltmeleri (her biri: eksik -> test -> değişiklik -> korunan)

```
1. Kimlik provenance/coverage kapsamıyordu. Test:
   test_r1_multileg_input_identity_carries_provenance_that_run_input_does_not
   (aynı mumlar, pencere içi coverage iki aralık olarak kayıtlı: replay_input ve
   run_input AYNI, yeni multileg_input_sha256 FARKLI). Değişiklik: results'a
   açıkça adlandırılmış multileg_input_sha256 (şema multileg-input/v1:
   replay_input_sha256 + config kind/version + her bacak kayıtlı dataset
   kimliği, pencereyle örtüşen coverage, mum sayısı, tüketilen ve
   store-provenance fingerprint'leri + funding partition/kalite/gap/olay
   sayısı/tüketilen fingerprint; replay_input None ise None). Korunan:
   run_input_sha256 algoritması ve anlamı (cli.run_input_fingerprint) AYNI.
   Runner'ın sürüm işareti yoktur; uydurulmadı (kod revizyonu run_metadata'da).
2. identity_scope.config_sha256 metni "exactly as written" diyordu; kod ayrıştırılmış
   config'in kanonik JSON hash'ini alır (anahtar sırası, boşluk, BOM etkilemez).
   Test: test_r1_config_sha256_is_the_parsed_canonical_hash_not_a_byte_hash.
   Yalnız metin düzeltildi; hash sözleşmesi değişmedi.
3. report.md config/inputs'u göstermiyordu. results'a resolved_config ve
   input_evidence eklendi (report.py DEĞİŞMEDİ). Test: ..._markdown_shows_...
4. multileg-example writer hatası yakalanmamış traceback veriyordu (yanlış başarı
   YOKTU: config en son yazılıyordu). Değişiklik: config geçici dosya + os.replace
   ile en son görünür; hata exit 1 + "example NOT written ... incomplete, has no
   config and is left as is" (silme/onarım yok). Test: test_e_failed_example_...
Deterministik çıktı etkisi (aynı fixture, eski rapor %TEMP%\cql\multileg-run-1
ile yeni koşu): config, config_sha256, run_input_sha256, inputs, checks, errors,
limitations, does_not_prove ve bütün mevcut ekonomik results alanları AYNI;
değişen yalnız identity_scope.config_sha256 metni (+ yeni multileg_input_sha256
açıklaması) ve eklenen results anahtarları resolved_config, input_evidence,
multileg_input_sha256 -> deterministic_sha256 2f8dbf4f… -> 12b60e9f…. Eski
raporlar yeniden yazılmadı. Mevcut testlerde dar güncellemeler: C2
EXTRA_RESULT_KEYS'e üç yeni anahtar; C7'de kaynak etiketi değişince yeni
provenance alanlarının da değiştiği açıkça iddia edildi (ekonomi eşitliği aynı);
C12 komut kümesine multileg-doctor.
```

#### 19.16.3 `multileg-doctor` (isteğe bağlı, replay'siz)

```
python -m crypto_quant_lab.research multileg-doctor --config <dosya> --output <YENİ dizin>
Ortak hazırlık: multileg_store.prepare_store_backed_inputs(request) ->
  PreparedStoreInputs (istek doğrulama + her store kendi read snapshot'ında
  provenance/coverage/grid/funding okuması; bağlantılar dönmeden kapanır).
  run_store_backed_multileg_replay aynı fonksiyonu çağırıp ardından
  değişmemiş replay'i çağırır; public imza, ekonomi, snapshot kapsamı ve
  fingerprint anlamları aynı; her store yine tek kez sorgulanır.
Doctor: aynı parser + _require_store_files + prepare_store_backed_inputs +
  saf replay_input_fingerprint. run_multileg_replay, runner ve accounting
  (new_hedged_portfolio, apply_hedged_open/close, apply_perpetual_funding,
  mark_hedged_portfolio) ÇAĞRILMAZ.
Rapor (report v2, run_kind multileg-doctor, exit 0/1/2 aynı sözleşme):
  results.replay_executed=false, doctor_scope, not_evaluated,
  resolved_config, input_evidence (istenen pencere/timeframe/as_of, beyan
  edilen ve kayıtlı dataset/source, coverage, sayılar, fingerprint'ler;
  funding yalnız partition+coverage, source_recorded=false),
  replay_input_sha256, multileg_input_sha256, identity_scope.
  Final equity, fill, funding cashflow, realized/unrealized YOK.
NOT_EVALUATED (status "skipped", detail "NOT_EVALUATED: ..."; yeni status yok):
  economics.solvency, replay.core_validation (bacak/enstrüman eşleşmesi, özdeş
  open_time grid'leri, funding olay kimliği/sırası/duplicate ve settlement,
  intent'lerin mum kullanılabilirliğine göre kontrolü), economics.results.
Doctor PASS yalnız o okuma anındaki listelenen kontrollerdir; replay doctor
raporunu okumaz, doctor gerektirmez, girdisini her koşuda yeniden doğrular.
Store'lar arası atomik snapshot yoktur.
```

#### 19.16.4 Kabul kanıtı (tests/test_research_multileg_doctor.py, 21 öğe)

```
R1  B düzeltmeleri + composite 401.6583 (ters sıra: aynı değer, farklı kimlik),
    perpetual spread 401.8985, boş ondalık reddi, output çakışmaları (replay ve
    doctor), markdown içeriği, multileg_input kapsamı
R2  gerçek store'lar (spot.db, perp.db, f_t3.db açıldı); replay/runner/accounting
    sentinel çağrı listesi BOŞ; results'ta replay-only alan yok; doctor ve
    replay aynı replay_input/multileg_input, farklı deterministic
R3  8 bozuk girdi (kaynak, index_price, kapsam dışı, eksik mum, toplanmamış ve
    başka partition funding, eksik DB, desteklenmeyen model) -> iki komutta
    birebir aynı hata; doğrulanmış boş funding ikisinde de geçer
R4  3 birim (300 > 200 nakit): doctor succeeded + solvency NOT_EVALUATED,
    replay "ValueError: insufficient spot cash"
R5  doctor hiç koşmadan replay N2 402.0101; sorgular ["spot","usdm_perpetual"],
    open_read_only ["spot.db","perp.db"] (iki kez okuma yok)
R6  doctor PASS sonrası spot.db değişince replay_input farklı; perp.db başka
    kaynakla değişince replay provenance_mismatch
R7  başarı/hata sonrası bayt+mtime aynı, -wal/-shm yok, bağlantılar serbest
R8  farklı cwd + göreli config, düşmanca Decimal ortamı, ağ engeli, alias reddi
R9  iki çıktı dizininde aynı doctor deterministic; funding değeri değişince
    replay_input ve multileg_input değişir
R10 ekonomik değerler mevcut testlerde (N1–N7, 401.7076, 401.8591) değişmeden geçer
R11 22 accounting + 97 config + replay/store/source testleri; tam suite 2789 passed
R12 import yan etkisiz (ayrı süreç); describe None / fingerprint hatası iki
    komutta da failed, multileg_input None
E   enjekte writer hatası: exit 1, "example written" yok, config yok, writer'lar
    kapalı (dizin taşınabildi)
```

#### 19.16.5 Offline gösterim (PowerShell, 2026-09-25)

```
multileg-example -> %TEMP%\cql\multileg-example-2 (exit 0)
multileg-doctor  -> %TEMP%\cql\multileg-doctor-2 (exit 0; replay_executed=false;
                    deterministic c83bb396…, scratchpad'deki ayrı fixture ile aynı)
multileg-replay  -> %TEMP%\cql\multileg-run-3 (exit 0; 401.7076; multileg_input
                    39a36796…, doctor ile aynı)
CLOSE kaldırılmış -> %TEMP%\cql\multileg-run-open-end-2 (exit 0, 1 warning, 401.8591)
yanlış perpetual kaynağı -> multileg-doctor-bad-2 ve multileg-run-bad-2: ikisi de
                    exit 1, aynı [provenance_mismatch] hatası
```

#### 19.16.6 Denetim (ayrı öz-denetim turu, harici reviewer yok)

```
Arananlar: kaynak kanıtı uydurma (yok: kayıtlı dataset store'dan, funding
source_recorded=false), doctor'ın gizlice replay çağırması (R2 sentinel),
iki validator'ın ayrışması (tek parser + tek hazırlık; R3 birebir hata),
eski doctor sonucuna güvenme (R6), eksik kimliğin başarı sayılması (R12),
snapshot kaybı (hazırlık refactor'ı store başına tek snapshot ve aynı kapanma
anını korur; ST8 testleri geçer), output/input yazımı (R1, R7), aynı kodu iki
kez karşılaştırma (R2 kimlik eşitliği elle değerlerle birlikte).
Kalan küçük bulgu: multileg-example yazımı sırasında oluşan bir
FileNotFoundError (yalnız çıktı dizini değil) exit 2 olarak raporlanır; başarı
ilan edilmez ve config yazılmaz. Güç kaybı/kill -9 dayanıklılığı kanıtlanmadı.
```

Durumlar: Config-driven multi-leg research runner IMPLEMENTED + TESTED · Optional multileg-doctor IMPLEMENTED + TESTED (yalnız sentetik store'larla) · Real-market run NOT PERFORMED · Multi-leg strategy NOT IMPLEMENTED · Faz 7 NOT COMPLETE · FAZ6C NOT COMPLETE · FAZ6D NOT STARTED.

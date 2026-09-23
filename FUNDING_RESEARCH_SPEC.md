# FUNDING_RESEARCH_SPEC

Bu doküman, Faz 7 — İlk Funding/Basis araştırmasının **ilk çalıştırılabilir, leakage-safe dikey dilimini** tanımlar ve kapatır. Faz 7'nin tamamı bu dilimle TAMAMLANMIŞ SAYILMAZ.

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
     eksiktir.
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
   onun kalite/finalization sözleşmesi.
2. Aynı mum kapanış zamanında hem spot hem perpetual fiyatını karar
   anında gösterecek, zaman eşleşmesini birebir (yuvarlama/doldurma
   olmadan) yapan ikinci bir zaman kapılı görünüm; spot ve perpetual
   market kimliklerinin karışmasını engelleyen partition kuralları.
3. Basis tanımı (işaret ve oran formülü: (perp - spot) / spot vb.) için
   kaynak ve proje kararı.
Bunlar yokken basis ÜRETİLMEZ ve TAHMİN EDİLMEZ.
```

## 7. Sonuçların Anlamı

Üretilen değerler (WindowResult, Stage-1/Stage-2/annualized metrikler, Trial/TrialGroup) **araştırma metrikleridir**: kârlılık kanıtı, canlı işlem onayı veya risk kararı DEĞİLDİR. Gerçek veriyle çalıştırma için perpetual mumlarının store'a güvenilir biçimde yüklenmesi gerekir (§2.4).

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

USDⓈ-M perpetual kline ingestion (GET /fapi/v1/klines) + kalite sözleşmesi — gerçek funding ve perpetual mumlarıyla bu dilimi yerel veride çalıştırabilmenin ve basis'in ilk önkoşulu.

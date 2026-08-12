# PROJECT RULES

Bu doküman, Local Crypto Quant Research + Execution Platform projesinin uyulması zorunlu temel kurallarını tanımlar. Bu kurallar tüm fazlarda geçerlidir ve ihlal edilemez.

## Geliştirme Prensipleri

- Proje modüler geliştirilecek.
- Her adım küçük ve test edilebilir olacak.

## Sermaye ve Risk Güvenliği

- Gerçek para ile işlem, paper trading ve backtest aşamaları tamamlanmadan kesinlikle yapılmayacak.
- Yeni model/strateji doğrudan live'a alınmayacak.
- Sıra kesinlikle şu şekilde olacak: önce backtest, sonra walk-forward/out-of-sample, sonra paper/shadow, en son küçük live.
- API anahtarlarında withdrawal izni kullanılmayacak.
- Emergency stop, max drawdown, max daily loss ve data/API health kontrolleri ileride zorunlu olacak.

## Karar Verme Mimarisi

- LLM doğrudan al/sat kararı vermeyecek.
- Nihai trade kararı sayısal model + deterministik risk engine üzerinden geçecek.
- Risk Engine hiçbir model tarafından bypass edilemeyecek.
- "NO TRADE" geçerli ve önemli bir sonuç olacak.

## Backtest ve Araştırma Bütünlüğü

- Her strateji maliyetler dahil backtest edilmeden sisteme alınmayacak.
- Look-ahead bias, data leakage ve survivorship bias önlenmeye çalışılacak.
- Commission, spread, slippage ve funding maliyetleri hesaba katılacak.

## Claude Code Çalışma Kuralları

- Claude Code kendiliğinden proje kapsamını genişletmeyecek.
- Yalnızca verilen görevi yapacak.
- Mevcut çalışan yapıyı gereksiz yere değiştirmeyecek.
- Her görev sonunda hangi dosyaları oluşturduğunu/değiştirdiğini kısa şekilde raporlayacak.

# ARCHITECTURE

Bu doküman, Local Crypto Quant Research + Execution Platform'un yüksek seviye katman mimarisini tanımlar. Detay tasarım ve implementasyon kararları ilgili faz görevlerinde ele alınacaktır.

## Katmanlar

1. **Data Layer**
   Piyasa verisinin canlı/ham olarak alınmasından sorumlu katman.

2. **Historical Store / Feature Store**
   Geçmiş verinin ve türetilmiş feature'ların saklandığı katman.

3. **Research Layer**
   Hipotez üretimi, keşifsel analiz ve strateji araştırmasının yapıldığı katman.

4. **Backtest / Validation Layer**
   Stratejilerin geçmiş veri üzerinde maliyetler dahil test edildiği ve doğrulandığı katman.

5. **Strategy & Model Layer**
   Sinyal üreten sayısal stratejilerin ve modellerin bulunduğu katman.

6. **Decision Layer**
   Strateji/model çıktılarının nihai trade kararına dönüştürüldüğü katman.

7. **Risk Engine**
   Tüm kararların geçmek zorunda olduğu, bypass edilemeyen deterministik risk kontrol katmanı.

8. **Execution & Position Management**
   Onaylanmış kararların emirlere dönüştürülmesi ve pozisyon yönetimi.

9. **Monitoring / Logging**
   Sistemin sağlığının, performansının ve işlemlerin izlenip kayıt altına alındığı katman.

10. **Meta / Controlled Self-Improvement**
    Sistemin kontrollü ve denetlenebilir şekilde kendini iyileştirmesine olanak tanıyan katman.

11. **LLM / RAG Research Assistant**
    LLM ve RAG tabanlı araştırma yardımcı katmanı.

## Önemli Not: LLM'in Rolü

LLM, bu platformda **ana trading karar vericisi değildir**. LLM'in rolü yalnızca:

- Araştırma yapmak,
- Hipotez üretmek,
- Raporlama yapmak

ile sınırlıdır. Nihai al/sat kararı; sayısal model, Decision Layer ve Risk Engine üzerinden, deterministik ve denetlenebilir şekilde verilir. LLM hiçbir koşulda doğrudan trade emri tetiklemez veya Risk Engine'i bypass edemez.

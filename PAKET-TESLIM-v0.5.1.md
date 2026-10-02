# JSON API Forge v0.5.1 — Paket teslim özeti

Tarih: 6 Eylül 2026. Dört branch için birbirinden bağımsız, tam kaynak ZIP'i
hazırlandı. Her paket kendi workflow'larını, lisansını ve SHA-256 manifestini
içerir. Derleme klasörleri, çalışma verileri ve gizli anahtarlar teslim paketine
alınmaz. GitHub'a push yapılmadı; yüklemeyi kullanıcı yapacak.

## Paketler

| GitHub branch | Kaynak paketi | İçerik |
| --- | --- | --- |
| `main` | `JSON-API-Forge-main-v0.5.1.zip` | Sunucu, TypeScript istemcisi, güvenlik ve app dayanıklılığı |
| `Editor` | `JSON-API-Forge-Editor-v0.5.1.zip` | Qt Editör, ZIP plugin yükleme, GUI ve Release workflow'ları |
| `python-library` | `JSON-API-Forge-python-library-v0.5.1.zip` | Bağımsız senkron/asenkron Python SDK |
| `exampleApps` | `JSON-API-Forge-exampleApps-v0.5.1.zip` | 25 örnek uygulama ve kopyalama/paketleme araçları |

ZIP'lerin dış SHA-256 değerleri ayrı `SHA256SUMS-v0.5.1.txt` dosyasındadır.
Her ZIP'in içindeki `MANIFEST.sha256`, o branch'in kaynak dosyalarını doğrular.

## GitHub workflow bulgusu ve düzeltme

Kontrol edilen son main commit'i:
`0299e64e821adbf7309e854214e35f799b5f11aa`.

- [CI — 33741276365](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/runs/33741276365): başarısız.
- [Server and portable platform builds — 33741276219](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/runs/33741276219): başarısız.
- [CodeQL — 33741276435](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/runs/33741276435): başarılı.

İki başarısız workflow'un ortak nedeni, `.github/workflows/codeql.yml`,
`pyproject.toml` ve `requirements-dev.txt` dosyaları değiştikten sonra manifest
hash'lerinin güncellenmemesiydi. Paketlerde güncel CodeQL referansları ve
Twine sürüm sınırları korundu; manifestler son kaynak içeriklerinden yeniden
üretildi. Manifest kontrolü devre dışı bırakılmadı.

Manifest aracı ayrıca Git klasörü olmayan açılmış ZIP'lerde doğru dosyaları
bulacak; yeni dosyaları dahil edecek ve silinmiş eski dosyaları hariç tutacak
şekilde düzeltildi. Temiz checkout kopyalarında workflow kaynak-sahipliği
kontrolleri, YAML/yerleşik Python/Bash sözdizimi ve SHA sabitlemeleri doğrulandı.

Diğer branch'lerin GitHub'daki son build ve CodeQL sonuçları başarılıydı:
[Editor build](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/runs/33024648154),
[Python SDK build](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/runs/33024652208),
[Example applications](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/runs/33024659656).
Bu sonuçlar önceki commit'lere aittir; bu teslimdeki v0.5.1 değişikliklerinin
GitHub üzerinde çalıştırıldığı anlamına gelmez.

## Attacker → Defender → yeniden Attacker çalışması

| İncelenen risk | Uygulanan savunma ve tekrar kontrol |
| --- | --- |
| JWKS üzerinden özel ağa erişim, DNS yönlendirmesi, sınırsız yanıt | Güvenli egress taşıması, özel ağ/redirect/proxy politikası, yanıt sınırı ve politika bazlı önbellek |
| Pahalı düzenli ifadeler ve kontrol karakteri/header enjeksiyonu | Lineer regex motoru, sınırlı girdiler, desteklenmeyen ifadelerde kapalı davranış, sıkı URL/CORS/header denetimleri |
| Bir app'in bozuk manifesti, route kurulumu veya başlangıç hatası | App bazında karantina ve kaynak temizliği; diğer app'lerin çalıştığını doğrulayan gerçek HTTP testleri |
| Dosya yolundan dizin dışına çıkma, symlink, yarım yazma ve medya üzerine yazma | Uygun platformlarda dizin tanımlayıcısı üzerinden erişim, sınırlı okuma, atomik dosya değişimi ve çakışmada 409 |
| SDK'da çok katmanlı URL kodlaması, güvensiz header ve sınırsız retry | Kodlama/yol denetimi, ayrılmış header koruması, sonlu timeout/backoff sınırları, gözlemci sorgu gizliliği ve hata redaksiyonu |
| Plugin ZIP'lerinde traversal, symlink, çakışan adlar ve arşiv bombaları | Dosya/sıkıştırma sınırları, CRC ve SHA-256, manifest/API denetimi, basılabilir adlar; içe aktarılan plugin ayrı onay verilene kadar kapalı |
| Graph bağlantısı çizilirken sahne yenilenmesiyle geçersiz bellek erişimi | Callback öncesi geçici bağlantıyı temizleme; sahne değişiminde sürüklemeyi iptal; gerçek fare olayı testi ve ASan/UBSan |
| Örnek paketlerinde sınırsız bellek, değişen kaynak ve hedef çakışması | Akışla ZIP yazma, dosya/toplam sınırları, kaynak kimliği kontrolü, benzersiz geçici dosyalar ve güvenli kurulum hedefleri |

Bu liste yerel inceleme ve test bulgularıdır. GitHub bağlantısında Code Scanning
alert envanteri/SARIF erişimi sağlanamadığı için GitHub'daki bütün kritik
uyarıların kapandığı veya sıfır açık kaldığı iddia edilmiyor. CodeQL job'ının
başarılı olması, alert sayısının sıfır olması demek değildir.

## Ana yazılım davranışı

- `forge dev --reload`, yeni app dizinlerini, Python hook'larını, JSON/YAML
  yapılandırmasını ve app-local `.env` değişikliklerini otomatik uygular.
  Flask debug gibi geliştirme worker'ını otomatik yeniden başlatır; elle
  restart gerekmez. Bu işlem sıfır kesintili canlı app değiştirme değildir.
- Hatalı app yüzünden sağlıklı app'lerin başlangıcı iptal edilmez. Bilinen
  bozuk app prefix'leri 503 döndürür. Liveness çalışır; readiness sorunu gösterir.
- Prefix eşleştirme önceden hazırlanır; SDK ve örnek ZIP üretiminde bellek
  kullanımına sınır getiren akış/buffer iyileştirmeleri vardır.
- Üretimde otomatik reload açılmaz. Ortak güvenlik veritabanı, süreç sonlandırma
  ve native crash gibi sınırlar için [ayrıntılı davranış belgesine](docs/44-Development-Reload-and-App-Isolation.md) bakın.

## Editor ve Release çıktıları

Editör ayarlarında kalıcı timeout/retry/yanıt boyutu ve grafik etkileşimi
kontrolleri bulunur. Oturum yenileme sırasında kaydedilmemiş belge korunur;
kaydetme yarışı, revizyon çatışması ve founder kurulum belirsizliği ele alınır.
Grafikte sağ tuşla kaydırma, context menu ve sonlu zoom değerleri düzeltilmiştir.

Plugin menüsünden `.zip` içe aktarma desteklenir. Native plugin kodu, etkin
olduğunda kullanıcı yetkileriyle çalışır; SHA-256 bütünlük kontrolü bir yayıncı
imzası veya işletim sistemi sandbox'ı değildir. Kullanıcı yalnızca güvendiği
plugin'i etkinleştirmelidir.

Editor build workflow'u aşağıdaki altı hedefin her birinde Release üretir:

| Platform | Mimari | Taşınabilir | Kurulum |
| --- | --- | --- | --- |
| Linux | x64, ARM64 | Her mimari için ZIP | Qt IFW `-setup.run` |
| Windows | x64, ARM64 | Her mimari için ZIP | Qt IFW `-setup.exe` |
| macOS | Intel, Apple Silicon | Her mimari için ZIP | Qt IFW `-setup.dmg` |

Qt IFW araçları resmi indirmelerden SHA-256 doğrulanarak kurulur. Windows
kurulum işleminin bitmesi açıkça beklenir. macOS kurulum sonu başlatma yolu
gerçek `JSON-API-Forge-Editor.app` bundle adıyla eşleştirilir. Proje `LICENSE`
dosyası hem kurulumda lisans kabul ekranına hem kurulan dosyalara eklenir.

Başarılı workflow sonunda Actions'tan
`JSON-API-Forge-Editor-v0.5.1-release-assets` artifact'ını indirin. İçinde altı
taşınabilir ZIP, altı installer, checksum'lar ve
`JSON-API-Forge-Editor-v0.5.1-all-platforms.zip` bulunur; bunları GitHub Release'e
ekleyebilirsiniz. Bu teslimde verilen dört kaynak ZIP'i, o derlenmiş Release
artifact'larının yerine geçmez.

Apple Silicon'da Editor native ARM64'tür; Qt IFW 4.8.1 macOS aracı Intel olduğu
için installer Rosetta gerektirir. İmzalama/notarization kimlikleri verilmediğinden
paketler imzalı/notarize edilmiş olarak sunulmuyor.

Kendi bilgisayarında derleyenler için Editor paketindeki
`QT-CREATOR-BUILD-GUIDE.md`; kit seçimi, preset, test, Qt runtime paketleme ve
Qt IFW installer üretimini anlatır.

## Doğrulama sonuçları

| Kontrol | Bu teslimde sonuç |
| --- | --- |
| Main Python 3.12 testleri | 128 geçti, harici servis gerektiren 7 test atlandı |
| Main kapsam | %79,63; tüm kritik modül eşikleri geçti |
| SDK Python 3.12 testleri | 46 geçti; kapsam %87,19 |
| SDK–gerçek main sözleşmesi | 1 test geçti |
| Örnek uygulamalar | 25/25 schema, CRUD, RPC, idempotency, realtime, media ve public-data smoke geçti |
| Örnek paketleme | 3 regresyon testi, deterministik iki ZIP karşılaştırması ve Bash kurulum/kötü hedef kontrolü geçti |
| Editor Linux Release | Temiz derleme ve 26 Qt test sonucu geçti |
| Editor Linux Debug + ASan/UBSan | Derleme ve aynı 26 Qt test sonucu geçti |
| Main/SDK Python dağıtımı | Wheel ve sdist üretimi; Twine kontrolleri geçti |
| Bandit | Main, SDK ve örnek araçlarında raporlanan medium/high bulgu yok |
| pip-audit | İncelenen main/SDK bağımlılık ortamlarında bilinen açık bulunmadı; PyPI'de bulunmayan yerel main paketi kaynak olarak ayrıca incelendi |
| Kaynak ZIP doğrulaması | Her branch'te manifest; temiz checkout sözleşmeleri; açılmış ZIP'te dosya listesi ve hash kontrolü |

Bu oturumda Windows/macOS/ARM64 runner'ları, OCI/Alpine dağıtımları ve yedi
harici servis testi çalıştırılmadı. TypeScript test komutunun önceki denemesinde
ağ onayı tamamlanmadığı için bu teslimde TypeScript testlerinin geçtiği
iddia edilmiyor. İlgili zorunlu Actions test/build adımları workflow'larda korunur.

## Yükleme sırası

1. Her ZIP'i açın ve üstteki tek paket klasörünün **içeriğini** ilgili branch'in
   köküne koyun. `.github` klasörünü de yükleyin; ZIP'in kendisini repo kaynağı
   gibi yüklemek workflow'ları güncellemez.
2. Önce `main`, sonra `Editor`, `python-library` ve `exampleApps` yüklenmelidir.
   SDK ve examples workflow'ları gerçek `main` branch'ini ayrıca checkout eder.
3. Editor branch'inden şu iki eski dosyayı silin:
   `editor/packaging/windows/installer.nsi` ve `editor/packaging/linux/control`.
   Yeni ZIP'te bulunmamaları, GitHub'a üstüne yükleme yapıldığında otomatik
   silinmelerini sağlamaz. Özellikle NSIS dosyasının kalması preflight'ı durdurur.
4. Actions'ta yeni commit'lere ait build ve CodeQL sonuçlarını kontrol edin.
   Editor release-assets artifact'ını ancak yeni altı platform işi ve sanitizer
   işi tamamlandıktan sonra Release'e ekleyin.

Kaynakta bundan sonra dosya değiştirirseniz yüklemeden önce
`python scripts/check_manifest.py --write` ve ardından
`python scripts/check_manifest.py` çalıştırın. Aksi halde manifest kontrolü
dosya ile checksum arasındaki farkı tekrar haklı olarak reddeder.

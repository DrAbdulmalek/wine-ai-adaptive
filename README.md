# wine-ai-adaptive

[![CI](https://github.com/DrAbdulmalek/wine-ai-adaptive/actions/workflows/ci.yml/badge.svg)](https://github.com/DrAbdulmalek/wine-ai-adaptive/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

مكتبة Python لتخزين المعرفة التشخيصية الخاصة بتطبيقات Wine، مبنيّة فوق SQLite بتصميم مُحصّن يقبل البيانات المنظمة فقط ويمنع تخزين أي محتوى خام.

> **النواة الكاملة للمشروع الأصلي:** إلى جانب `KnowledgeStore` المحصّنة تتضمن الحزمة مكونات المشروع المحلي الأصلية: `Evidence`، جرد البيئة، محلل سجلات Wine، فحص توافق كروت الرسوميات عبر Vulkan، مزوّد Ollama المحلي (قراءة جرد فقط، دون إرسال أي prompts)، وجسر `KnowledgeBridge` — كلها offline-first وبمكتبة Python القياسية فقط.
>
> **إضافات المراجعة الخارجية (v0.2.0):** استخراج رموز الأخطاء (HRESULT/NTSTATUS/Win32) وتصنيفها من نص السجل دون تخزين أي نص خام، طبقة استعلام للقراءة فقط، ترحيل مخطط ذري إلى v2 مع حقلَي `error_code` و`prefix_id`، لقطات بيئة منظمة (كشف المشغّل: Steam/Lutris/Bottles/Heroic باسم مجرد للبادئة فقط)، ومعرفة ثابتة مُرفقة لربط مورّد GPU بأخطاء الرسوميات الشائعة.

## الحدود التشغيلية (Safety boundaries)

- لا إصلاح تلقائي لـWine أو النظام، ولا مساس بـprefix أو السجل أو DLL.
- لا صلاحيات root، ولا مكالمات مزوّد ذكاء اصطناعي بعيد افتراضيًا.
- لا تخزين سجلات خام: حقول منظمة بقوائم مسموحة فقط، وقيود `CHECK` خط الدفاع الأخير.
- فشل آمن (fail-closed) في المسارات الحساسة: روابط رمزية وملفات غير اعتيادية ومخططات غير متوافقة تُرفض.
- لا كتابة إلى GitHub من كود التشغيل.

## الأداة السطرية (CLI)

```bash
pip install -e .
wine-ai status          # ملخص حالة بيئة Wine وVulkan بأدلة مُصنّفة
wine-ai status --json   # نفس المخرجات بصيغة JSON منظمة
```

الأداة للقراءة فقط: لا تعدّل شيئًا، وتفشل بأمان إذا لم تُوجد الأدوات.

## المزايا الأساسية

- **تخزين منظم فقط:** لا تُقبل سجلات خام (raw logs) ولا مسارات ملفات ولا نصوص عشوائية؛ كل حقل يُتحقق من صحته مقابل قوائم مسموحة (allow-lists).
- **إدراج ذري للدفعات:** `add_records_atomic` تُدخل الدفعة كاملة أو لا تُدخل شيئًا، بحد أقصى 100 سجل في الدفعة.
- **تحصين على مستوى الملف:** تُنشأ قاعدة البيانات بـ`O_CREAT | O_EXCL | O_NOFOLLOW` بصلاحيات `0600` منذ لحظة إنشائها، والمجلد الأب المباشر يُفرَض على `0700` مع التحقق من ملكيته للمستخدم الحالي.
- **رفض المسارات غير الآمنة:** الرابط الرمزي أو الملف غير العادي أو الملف المملوك لمستخدم آخر — عند مسار قاعدة البيانات أو ملفاتها الجانبية `-wal/-shm/-journal` — يُرفض بدل متابعته.
- **إدارة إصدارات المخطط:** عبر `PRAGMA user_version` مع رفض فتح قواعد بيانات من إصدارات مخطط غير مدعومة.
- **التحقق من بصمة المخطط:** عند كل فتح يُطابق تعريف الجدول المخزّن وبيانات الأعمدة القالب المتوقع؛ قاعدة بإصدار صحيح ومخطط غير متوافق (أعمدة ناقصة أو زائدة أو قيود `CHECK` مفقودة) تُرفض.
- **تهيئة ذرية وكشف الحالات الجزئية:** إنشاء الجدول وختم الإصدار يتمان في معاملة واحدة، وأي حالة جزئية (جدول بلا ختم أو ختم بلا جدول) تُرفض صراحةً.
- **قيود على مستوى قاعدة البيانات:** قيود `CHECK` في SQLite تعمل كخط دفاع أخير حتى لو تجاوزت الشيفرة طبقة التحقق البرمجية.
- **ترحيل مخطط ذري:** قواعد الإصدار 1 تُرقّى تلقائيًا إلى الإصدار 2 عبر معاملة واحدة تتحقق أولًا من بصمة SQL والأعمدة؛ أي خلل يُبقي القاعدة على v1 دون مساس (fail-closed).
- **رموز أخطاء منظمة:** `error_code` يقبل فقط نمط `0x…` السداسي أو أسماء `STATUS_*`/`ERROR_*` — لا نص حر ولا مسارات.
- **طبقة استعلام للقراءة فقط:** `find_by_error_category` و`find_by_gpu_vendor` و`find_by_evidence_status` و`find_by_error_code` و`category_counts` و`correlate_error_codes` — كلها SQL مُعامَلة (parameterized) بلا SQL حر.

## نموذج الأمن (Security model)

- محلي أولًا وبلا اتصال افتراضيًا؛ تكامل Ollama اختياري ويدعم الجرد المحلي فقط.
- لا سجلات خام ولا prompts ولا مسارات بادئات تُخزَّن؛ الحقول المنظمة فقط تمر عبر قوائم مسموحة.
- قاعدة البيانات وملفاتها الجانبية خاصة (`0600`/`0700`).
- خطر الروابط الرمزية مُقلَّص، لكن TOCTOU المتبقي موثّق (اتصال SQLite عبر المسار لا عبر file descriptor).
- التحقق من المخطط عميق؛ الترحيل ذري وفاشل آمن (fail-closed).
- لا إصلاح تلقائي لـWine، لا root، ولا بديل سحابي.

## القيود الأمنية المعلنة

توثيق صادق لما لا يضمنه التحصين الحالي:

- مكوّنات المسار الأعلى من المجلد الأب المباشر لا تُفحص ضد الروابط الرمزية.
- سباقات check-then-use (TOCTOU) لا يمكن إزالتها كليًا من فضاء المستخدم؛ `O_NOFOLLOW` والتحقق قبل الفتح يضيّقان نافذة السباق دون إغلاقها تمامًا.
- ملفات الـjournal العابرة أثناء التشغيل تُنشأ بصلاحيات مشتقة من umask للتطبيق المضيف؛ على الأنظمة متعددة المستخدمين يُنصح بتشغيل التطبيق بـ`umask 077`.
- التهيئة تغيّر umask العملية مؤقتًا؛ أنشئ مخازنك قبل الاعتماد على إنشاء ملفات متزامن في خيوط أخرى.

## هيكل المشروع

```text
wine-ai-adaptive/
├── src/wine_ai/
│   ├── __init__.py
│   ├── cli.py                       # أداة wine-ai السطرية (قراءة فقط)
│   ├── command.py                   # غلاف subprocess آمن للقراءة فقط
│   ├── evidence.py                  # EvidenceStatus و Evidence
│   ├── knowledge/
│   │   ├── __init__.py
│   │   ├── bridge.py                # جسر persist_findings (مع error_code/prefix_id)
│   │   └── store.py                 # وحدة KnowledgeStore المحصّنة (مخطط v2 + طبقة استعلام)
│   ├── diagnostics/
│   │   ├── log_parser.py            # تصنيف أنماط سجلات Wine
│   │   └── codes.py                 # استخراج وتصنيف رموز الأخطاء (HRESULT/NTSTATUS/Win32)
│   ├── environment/
│   │   ├── inventory.py             # جرد البيئة (قراءة فقط)
│   │   └── snapshot.py              # لقطات بيئة منظمة (مشغّل/توزيعة/إصدار، باسم بادئة مجرد)
│   ├── gpu_compatibility/
│   │   ├── probe.py                 # فحص Vulkan/GPU
│   │   ├── vendor_data.py           # تحميل المعرفة الثابتة للمورّدين
│   │   └── data/vendor_map.json     # معرفة مورّد GPU المُرفقة (offline)
│   ├── providers/ollama/client.py   # جرد Ollama المحلي فقط
│   ├── reporting/                   # حزمة مؤطّرة (توسعة مستقبلية)
│   └── security/                    # حزمة مؤطّرة (توسعة مستقبلية)
├── tests/unit/
│   ├── test_knowledge_store.py       # اختبارات الوحدة
│   ├── test_knowledge_hardening.py   # اختبارات التحصين
│   ├── test_knowledge_security.py    # اختبارات أمنية مستقلة (symlink، صلاحيات، مخطط)
│   ├── test_schema_migration.py      # اختبارات الترحيل الذري v1→v2
│   ├── test_store_queries.py         # اختبارات طبقة الاستعلام
│   ├── test_knowledge_bridge.py      # اختبارات الجسر
│   ├── test_error_codes.py           # اختبارات رموز الأخطاء
│   ├── test_vendor_data.py           # اختبارات معرفة المورّدين
│   ├── test_environment_snapshot.py  # اختبارات لقطات البيئة
│   ├── test_log_parser.py            # اختبارات محلل السجلات
│   ├── test_gpu_probe.py             # اختبارات فحص GPU
│   └── test_ollama_client.py         # اختبارات عميل Ollama (مُحاكاة)
├── docs/
│   ├── SECURITY_AUDIT.md             # تقرير تدقيق التحصين
│   ├── THREAT_MODEL.md               # نموذج التهديد: تهديد ← تحصين ← اختبار
│   └── AGENT_POLICY.md               # سياسة عمل الوكلاء والمساهمين
├── examples/
│   ├── basic_usage.py
│   └── end_to_end.py                 # خط كامل: رموز ← لقطة ← تخزين ← استعلام
├── .github/workflows/ci.yml      # GitHub Actions CI
├── SECURITY.md                   # سياسة الأمن وحدود التشغيل
├── Makefile                      # test / compile / clean
├── pyproject.toml
└── README.md
```

## التثبيت

```bash
git clone https://github.com/DrAbdulmalek/wine-ai-adaptive.git
cd wine-ai-adaptive
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## مثال سريع

```python
from wine_ai.knowledge.store import KnowledgeStore

# يتم إنشاء قاعدة البيانات بصلاحيات 0600 تلقائيًا
store = KnowledgeStore("~/.local/share/wine-ai/diagnostics.sqlite3")

record_id = store.add_record(
    wine_version="11.19",
    distro="Manjaro",
    error_category="graphics",
    evidence_status="PARTIALLY_PROVEN",
    confidence="low",
    gpu_vendor="amd",
)

# إدراج دفعة ذرية (كل السجلات أو لا شيء)
store.add_records_atomic([
    {
        "wine_version": "10.15",
        "distro": "Arch Linux",
        "error_category": "missing_module",
        "evidence_status": "UNPROVEN",
        "confidence": "medium",
        "gpu_vendor": "nvidia",
    },
])

for record in store.list_records(limit=10):
    print(record)
```

مثال كامل قابل للتشغيل: `python examples/basic_usage.py`

## تصدير/استيراد مجهّل للسجلات (Portability)

مشاركة المعرفة التشخيصية بين المستخدمين أو المجتمعات دون أي تسريب:

- **الافتراضي:** بلا `prefix_id`، والطوابع الزمنية مختصرة إلى `YYYY-MM` (`created_at_ym`).
- **اختياري:** `include_prefix_id=True` لطلب معرّف البادئة، أو `include_time=False` لحذف الوقت كليًا.
- **الاستيراد:** يتحقق من كل سجل عبر `_prepare_record` نفسها قبل أي إدخال، وإدراج الدفعات على دفعات `MAX_BATCH_SIZE`.
- **السقف:** 1000 سجل كحد أقصى في كل اتجاه. لا نص خام ولا مسارات خاصة — بحسب التصميم.

```python
from wine_ai.knowledge.store import KnowledgeStore
from wine_ai.portability import export_to_file, import_package_file

store = KnowledgeStore("data/knowledge.sqlite3")
export_to_file(store, "export.json")                          # الافتراضي: بلا prefix_id، YYYY-MM فقط
export_to_file(store, "export.json", include_prefix_id=True)  # طلب صريح
import_package_file(store, "export.json")
```

التفاصيل الكاملة للصيغة والحدود والمخاطر المتبقية: `docs/portability.md`. ربط الأداة السطرية مؤجَّل عمدًا — واجهة مكتبة فقط.

## تشغيل الاختبارات

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## نموذج البيانات

| الحقل | القيم المسموحة |
|---|---|
| `error_category` | `missing_module`, `graphics`, `memory`, `startup`, `unknown` |
| `evidence_status` | `PROVEN`, `PARTIALLY_PROVEN`, `UNPROVEN`, `CONTRADICTED`, `BLOCKED` |
| `confidence` | `low`, `medium`, `high` |
| `gpu_vendor` | `intel`, `amd`, `nvidia`, `unknown` |
| `error_code` (اختياري) | `0x` + حتى 8 أرقام سداسية، أو اسم `STATUS_*`/`ERROR_*` بحروف كبيرة وأرقام وشرطات سفلية |
| `prefix_id` (اختياري) | اسم مجرد للبادئة بنمط آمن (لا مسارات، حتى 64 محرفًا) |

حقلا `wine_version` و`distro` نصان حرّان لكنهما يخضعان لنمط آمن يمنع المسارات والرموز الخبيثة (حتى 64 محرفًا).

### مصادر معرفة المورّدين

ملف `vendor_map.json` مكتوب يدويًا لهذا المشروع (لا كود ولا بيانات منسوخة من مشاريع خارجية)، مستوحى من المعرفة المجتمعية العامة المتوفرة في WineHQ AppDB وWinuxDB. المدخلات مؤشرات فرز استرشادية وليست إثباتًا لسبب الجذر.

## خارطة الطريق

- [x] طبقة استعلامات وتصفية حسب الفئة وحالة الدليل ورمز الخطأ
- [x] لقطات بيئة منظمة مرتبطة بالسجلات عبر `prefix_id`
- [ ] تصدير واستيراد مجهّل للسجلات للمشاركة بين الأجهزة
- [ ] محرك توصيات تكيفي يبني على السجلات المثبتة (PROVEN)
- [ ] خادم MCP لتعريض المتجر لوكالات LLM المحلية (مستوحى من mcp-winedbg)

## الترخيص

MIT — انظر [LICENSE](LICENSE).

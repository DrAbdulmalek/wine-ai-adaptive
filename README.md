# wine-ai-adaptive

[![CI](https://github.com/DrAbdulmalek/wine-ai-adaptive/actions/workflows/ci.yml/badge.svg)](https://github.com/DrAbdulmalek/wine-ai-adaptive/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

مكتبة Python لتخزين المعرفة التشخيصية الخاصة بتطبيقات Wine، مبنيّة فوق SQLite بتصميم مُحصّن يقبل البيانات المنظمة فقط ويمنع تخزين أي محتوى خام.

## المزايا الأساسية

- **تخزين منظم فقط:** لا تُقبل سجلات خام (raw logs) ولا مسارات ملفات ولا نصوص عشوائية؛ كل حقل يُتحقق من صحته مقابل قوائم مسموحة (allow-lists).
- **إدراج ذري للدفعات:** `add_records_atomic` تُدخل الدفعة كاملة أو لا تُدخل شيئًا، بحد أقصى 100 سجل في الدفعة.
- **تحصين على مستوى الملف:** تُنشأ قاعدة البيانات بـ`O_CREAT | O_EXCL | O_NOFOLLOW` بصلاحيات `0600` منذ لحظة إنشائها، والمجلد الأب المباشر يُفرَض على `0700` مع التحقق من ملكيته للمستخدم الحالي.
- **رفض المسارات غير الآمنة:** الرابط الرمزي أو الملف غير العادي أو الملف المملوك لمستخدم آخر — عند مسار قاعدة البيانات أو ملفاتها الجانبية `-wal/-shm/-journal` — يُرفض بدل متابعته.
- **إدارة إصدارات المخطط:** عبر `PRAGMA user_version` مع رفض فتح قواعد بيانات من إصدارات مخطط غير مدعومة.
- **التحقق من بصمة المخطط:** عند كل فتح يُطابق تعريف الجدول المخزّن وبيانات الأعمدة القالب المتوقع؛ قاعدة بإصدار صحيح ومخطط غير متوافق (أعمدة ناقصة أو زائدة أو قيود `CHECK` مفقودة) تُرفض.
- **تهيئة ذرية وكشف الحالات الجزئية:** إنشاء الجدول وختم الإصدار يتمان في معاملة واحدة، وأي حالة جزئية (جدول بلا ختم أو ختم بلا جدول) تُرفض صراحةً.
- **قيود على مستوى قاعدة البيانات:** قيود `CHECK` في SQLite تعمل كخط دفاع أخير حتى لو تجاوزت الشيفرة طبقة التحقق البرمجية.

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
│   └── knowledge/
│       ├── __init__.py
│       └── store.py              # وحدة KnowledgeStore
├── tests/unit/
│   ├── test_knowledge_store.py       # اختبارات الوحدة
│   ├── test_knowledge_hardening.py   # اختبارات التحصين
│   └── test_knowledge_security.py    # اختبارات أمنية مستقلة (symlink، صلاحيات، مخطط)
├── docs/
│   ├── SECURITY_AUDIT.md             # تقرير تدقيق التحصين
│   ├── THREAT_MODEL.md               # نموذج التهديد: تهديد ← تحصين ← اختبار
│   └── AGENT_POLICY.md               # سياسة عمل الوكلاء والمساهمين
├── examples/
│   └── basic_usage.py
├── .github/workflows/ci.yml      # GitHub Actions CI
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

حقلا `wine_version` و`distro` نصان حرّان لكنهما يخضعان لنمط آمن يمنع المسارات والرموز الخبيثة (حتى 64 محرفًا).

## خارطة الطريق

- [ ] طبقة استعلامات وتصفية حسب الفئة والحالة ومستوى الثقة
- [ ] تصدير واستيراد مجهّل للسجلات للمشاركة بين الأجهزة
- [ ] محرك توصيات تكيفي يبني على السجلات المثبتة (PROVEN)

## الترخيص

MIT — انظر [LICENSE](LICENSE).

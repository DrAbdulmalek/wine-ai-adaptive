# wine-ai-adaptive

[![CI](https://github.com/DrAbdulmalek/wine-ai-adaptive/actions/workflows/ci.yml/badge.svg)](https://github.com/DrAbdulmalek/wine-ai-adaptive/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

مكتبة Python لتخزين المعرفة التشخيصية الخاصة بتطبيقات Wine، مبنيّة فوق SQLite بتصميم مُحصّن يقبل البيانات المنظمة فقط ويمنع تخزين أي محتوى خام.

## المزايا الأساسية

- **تخزين منظم فقط:** لا تُقبل سجلات خام (raw logs) ولا مسارات ملفات ولا نصوص عشوائية؛ كل حقل يُتحقق من صحته مقابل قوائم مسموحة (allow-lists).
- **إدراج ذري للدفعات:** `add_records_atomic` تُدخل الدفعة كاملة أو لا تُدخل شيئًا، بحد أقصى 100 سجل في الدفعة.
- **تحصين على مستوى الملف:** قاعدة البيانات تُنشأ بصلاحيات `0600` والمجلد الأب بصلاحيات `0700`.
- **إدارة إصدارات المخطط:** عبر `PRAGMA user_version` مع رفض فتح قواعد بيانات من إصدارات مخطط غير مدعومة.
- **قيود على مستوى قاعدة البيانات:** قيود `CHECK` في SQLite تعمل كخط دفاع أخير حتى لو تجاوزت الشيفرة طبقة التحقق البرمجية.

## هيكل المشروع

```text
wine-ai-adaptive/
├── src/wine_ai/
│   ├── __init__.py
│   └── knowledge/
│       ├── __init__.py
│       └── store.py              # وحدة KnowledgeStore
├── tests/unit/
│   ├── test_knowledge_store.py   # اختبارات الوحدة
│   └── test_knowledge_hardening.py  # اختبارات التحصين
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

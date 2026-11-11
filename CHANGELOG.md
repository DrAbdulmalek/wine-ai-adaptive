# Changelog

جميع التغييرات الملحوظة في هذا المشروع تُوثَّق هنا وفق
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
و[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-11

### Added
- وحدة `wine_ai.knowledge.store` مع الصف `KnowledgeStore` للتخزين التشخيصي المُحصّن فوق SQLite.
- إدراج ذري للدفعات عبر `add_records_atomic` بحد أقصى 100 سجل مع تراجع كامل عند أي فشل.
- تحقق صارم من المدخلات: قوائم مسموحة للفئات وحالات الدليل ومستويات الثقة وأنواع كروت الرسوميات.
- صلاحيات ملف مقيدة (`0600`) وإدارة إصدار مخطط قاعدة البيانات عبر `PRAGMA user_version`.
- 18 اختبار وحدة وتحصين (pytest).
- حزمة قابلة للتثبيت وفق src-layout (`pyproject.toml`) مع سير عمل CI على GitHub Actions.
- مثال استخدام جاهز للتشغيل في `examples/basic_usage.py`.

## [Unreleased]

### Security
- `.gitignore` يستبعد صراحةً مجلدات البيانات المحلية الحساسة `data/` و`.local-safety-backups/` (بيانات تشخيصية محلية ونسخ احتياطية أمنية — لا تُنشر أبدًا)، اتساقًا مع سياسة «لا نرفع السجل الخام، بل الخلاصة المهيكلة» ومن توصيات جلسة المراجعة الخارجية.

## [0.2.0] - 2026-11-11

### Added
- استخراج وتصنيف رموز أخطاء Wine/Windows عبر `wine_ai.diagnostics.codes`: أنماط HRESULT/NTSTATUS/Win32 مع خرائط تصنيف استرشادية إلى فئات `KnowledgeStore`، ودالة `extract_error_codes` تُعيد معرّفات منظمة فقط (بلا نص خام) وتتجاهل عناوين الذاكرة العادية.
- حقلان اختياريان في المخطط v2: `error_code` (سداسي أو اسم STATUS_*/ERROR_* بنمط آمن) و`prefix_id` (اسم بادئة مجرد بلا مسارات).
- ترحيل مخطط ذري v1→v2: تحقق صارم من بصمة SQL والأعمدة قبل المساس، وتراجع كامل يُبقي القاعدة على v1 عند أي فشل.
- طبقة استعلام للقراءة فقط في `KnowledgeStore`: `find_by_error_category`، `find_by_gpu_vendor`، `find_by_evidence_status`، `find_by_error_code`، `category_counts`، `correlate_error_codes` — كلها SQL مُعامَلة.
- `persist_findings` في الجسر يقبل `error_code`/`prefix_id` على مستوى الدفعة أو لكل نتيجة، مع تطبيع وتحقق قبل الكتابة.
- `wine_ai.gpu_compatibility.vendor_data` مع `data/vendor_map.json`: معرفة ثابتة مكتوبة يدويًا تربط مورّد GPU بأخطاء الرسوميات الشائعة (offline بالكامل).
- `wine_ai.environment.snapshot`: لقطات بيئة منظمة تقرأ فقط (كشف المشغّل Steam/Lutris/Bottles/Heroic من أسماء متغيرات البيئة، إصدار Wine، معرّف التوزيعة) وتُرجع اسم بادئة مجرد فقط.
- مثال `examples/end_to_end.py` يربط الرموز واللقطات والتخزين والاستعلام في خط واحد.
- ملفات `__init__.py` للحزم الجديدة عند الحاجة.

### Changed
- `pyproject.toml`: الإصدار 0.2.0، الترخيص بصيغة PEP 639 (`license = "MIT"` مع `license-files`) مع setuptools>=77، وإضافة `[tool.setuptools.package-data]` لتضمين `vendor_map.json` في الحزمة الموزعة.
- `list_records` تُرجع الآن الحقلين الجديدين `error_code`/`prefix_id` (بقيمة None للسجلات القديمة).

### Security
- تحقق أعمق من المخطط: يُقبل تعريف SQL القانوني للإصدارين v1 وv2 فقط، مع إلزامية مطابقة مجموعة أعمدة v2 العشرة بالضبط (النوع وNOT NULL وPK).
- اختبارات ترحيل جديدة: نجاح الترحيل مع حفظ الصفوف، فشل ذري يترك القاعدة سليمة، رفض SQL المتلاعب به، رفض أعمدة زائدة، رفض إصدار مستقبلي دون تعديل الملف، وثبات الصلاحيات تحت umask 022.
- إنشاء ملف قاعدة البيانات بـ`O_CREAT | O_EXCL | O_NOFOLLOW` بصلاحيات `0600` منذ لحظة الإنشاء (إزالة النافذة العابرة بصلاحيات أوسع).
- رفض الروابط الرمزية والملفات غير العادية والملفات المملوكة لمستخدم آخر عند مسار قاعدة البيانات وعند ملفاتها الجانبية `-wal/-shm/-journal`.
- فرض صلاحيات `0700` وملكية المستخدم الحالي على المجلد الأب المباشر.
- التحقق من بصمة المخطط (تعريف الجدول المخزّن + بيانات الأعمدة عبر `PRAGMA table_info`) عند كل فتح، ورفض قواعد بيانات بإصدار صحيح ومخطط غير متوافق.
- كشف حالات التهيئة الجزئية ورفضها صراحةً، وتنفيذ التهيئة الأولية في معاملة واحدة (`BEGIN IMMEDIATE`).
- تقييد ملفات `-wal/-shm/-journal` الموجودة إلى `0600`، مع توثيق سلوك SQLite في تنظيف الملفات القديمة عند الفتح.

### Added
- 15 اختبارًا أمنيًا مستقلًا في `tests/unit/test_knowledge_security.py` (إجمالي الحزمة 33 اختبارًا).
- `docs/SECURITY_AUDIT.md`: تقرير تدقيق التحصين بتصنيفات الأدلة (PROVEN/PARTIALLY_PROVEN/UNPROVEN/BLOCKED).
- `docs/THREAT_MODEL.md`: نموذج تهديد طبقة التخزين (13 تهديدًا) بخريطة تهديد ← تحصين ← اختبار ← حالة، من مراجعة أمنية خارجية.
- `docs/AGENT_POLICY.md`: سياسة عمل الوكلاء والمساهمين (المسموح/المحظور/قواعد ثابتة/شروط التوقف).
- اختبار `test_rejects_symlinked_sidecar_files`: رفض الروابط الرمزية عند مسارات الملفات الجانبية `-wal/-shm/-journal` دون المساس بالهدف (إجمالي الحزمة 34 اختبارًا).
- **استعادة مكونات المشروع الأصلي** من النسخة المحلية دون تعديلها: `evidence.py`، `command.py`، `diagnostics/log_parser.py`، `environment/inventory.py`، `gpu_compatibility/probe.py`، `providers/ollama/client.py`، `knowledge/bridge.py`، مع اختباراتها الأصلية (`test_knowledge_bridge.py`، `test_log_parser.py`، `test_gpu_probe.py`، `test_ollama_client.py`).
- أداة سطرية للقراءة فقط `wine-ai status [--json]` (`src/wine_ai/cli.py`) تعرض حالة بيئة Wine وVulkan بأدلة مُصنّفة، مع نقطة دخول `[project.scripts]`.
- `SECURITY.md`: سياسة أمن المشروع وحدوده التشغيلية وقناة الإبلاغ عن الثغرات.
- `Makefile`: أهداف `test` (unittest قياسي دون pytest) و`test-pytest` و`compile` و`clean` و`install` و`editable`.

### Changed
- `.gitignore` يستبعد ملفات SQLite الجانبية (`*.sqlite3-*`).
- README يوثق الضمانات الأمنية الفعلية وقيودها بدقة بدل الادعاءات المطلقة.
- **الحد الأدنى لـPython أصبح `>=3.11`** (مكونات المشروع الأصلي تستخدم `enum.StrEnum`)، وتحديث classifiers وإشارة CI؛ CI يختبر 3.11–3.13 ويضيف خطوة `compileall`.
- **إصلاح تلف في مشغّلات CI**: `branches: ain]` ← `branches: [main]` في `push` و`pull_request`.
- وصف الحزمة يعكس نطاقها الأوسع: نواة تشخيص محلية مُحصّنة لـWine على لينكس.

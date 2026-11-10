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

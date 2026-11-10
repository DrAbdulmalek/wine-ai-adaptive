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

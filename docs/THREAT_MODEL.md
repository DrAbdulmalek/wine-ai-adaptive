# نموذج التهديد — طبقة التخزين `KnowledgeStore`

يوثّق هذا الملف نموذج التهديد الخاص بطبقة التخزين التشخيصي، نتيجة
مراجعة أمنية خارجية (محادثة مراجعة مع نموذج DeepSeek) جرى فيها تشريح
سلوك الطبقة أمام مهاجم محلي، ثم مطابقة كل تهديد مع التحصين المنفَّذ
في `src/wine_ai/knowledge/store.py` والاختبار الذي يثبّته.

الأصل المراد حمايته: ملف قاعدة البيانات `*.sqlite3` وملفاتها الجانبية
(`-wal` و`-shm` و`-journal`) ومحتواها من السجلات التشخيصية المنظمة.

الخصم المفترض: مستخدم محلي آخر على نفس النظام متعدد المستخدمين، أو
عملية اخترقت تطبيقًا آخر يعمل بنفس المستخدم، يستطيع إنشاء روابط رمزية
وقراءة/كتابة ملفات المستخدم الحالي ضمن حدود صلاحياته.

## خريطة التهديدات والتحصينات

| # | التهديد | التحصين المنفَّذ | الموقع في الكود | الاختبار المثبِّت | الحالة |
|---|---|---|---|---|---|
| T1 | مسار قاعدة البيانات نفسه رابط رمزي لتوجيه الكتابة إلى ملف بريء | رفض صريح: `lstat` قبل الفتح + `O_NOFOLLOW` عند الإنشاء | `_verify_database_file`, `_create_database_file` | `test_rejects_symlinked_database_file`, `test_rejects_dangling_symlink_database_file` | PROVEN |
| T2 | المجلد الأب المباشر رابط رمزي | رفض صريح عبر `lstat` | `_verify_parent_directory` | `test_rejects_symlinked_parent_directory` | PROVEN |
| T3 | ملف غير عادي عند مسار القاعدة (مجلد/قناة FIFO/جهاز) | رفض كل ما ليس ملفًا اعتياديًا | `_verify_database_file` | `test_rejects_directory_in_place_of_database`, `test_rejects_non_regular_database_file` | PROVEN |
| T4 | مجلد أب بأذونات فضفاضة (قابل للقراءة من آخرين) | فرض `0700` وإصلاح الأذونات الموجودة | `__init__`, `_verify_parent_directory` | `test_tightens_insecure_parent_directory_permissions` | PROVEN |
| T5 | ملف أو مجلد مملوك لمستخدم آخر | رفض صريح بمقارنة `st_uid` مع `geteuid` | `_verify_parent_directory`, `_verify_database_file`, `_verify_sidecar` | لا يُختبر آليًا (يتطلب صلاحيات root) | PARTIALLY_PROVEN |
| T6 | انحراف أذونات ملف القاعدة عن `0600` | إنشاء بـ`O_CREAT \| O_EXCL` بوضع `0600` + إعادة فرض الأذونات عند كل فتح | `_create_database_file`, `_restrict_file_permissions` | `test_database_permissions_are_restricted` | PROVEN |
| T7 | قاعدة بيانات بختم إصدار مجهول أو مستقبلي | رفض أي إصدار خارج `{0, SCHEMA_VERSION}` | `_initialize` | `test_rejects_unknown_schema_version` | PROVEN |
| T8 | قاعدة بإصدار صحيح لكن بمخطط فاسد (أعمدة ناقصة/زائدة، قيود `CHECK` محذوفة) | مطابقة بصمة المخطط: نص `CREATE` المخزَّن + بيانات `PRAGMA table_info` | `_verify_schema` | `test_rejects_schema_without_check_constraints`, `test_rejects_schema_with_extra_column`, `test_rejects_schema_with_missing_column` | PROVEN |
| T9 | تهيئة جزئية (جدول بلا ختم إصدار أو ختم بلا جدول) نتيجة انهيار أثناء الإنشاء | كشف الحالة الجزئية ورفضها، والتهيئة داخل معاملة واحدة `BEGIN IMMEDIATE` مع تراجع كامل | `_initialize` | `test_rejects_partial_initialization`, `test_rejects_versioned_database_without_table` | PROVEN |
| T10 | ملفات جانبية `-wal/-shm/-journal` بترويسة رابط رمزي أو بأذونات فضفاضة تسرب بيانات | فحص كل مسار جانبي: رفض الروابط الرمزية وغير الاعتيادي والملكية الغريبة، وتقييد الموجود إلى `0600` | `_verify_sidecar`, `_restrict_file_permissions` | `test_rejects_symlinked_sidecar_files`, `test_stale_sidecar_files_are_cleaned_or_restricted`, `test_wal_mode_database_remains_usable` | PROVEN |
| T11 | سباقات check-then-use (TOCTOU) بين الفحص والفتح | تضييق النافذة: `O_NOFOLLOW`, `O_EXCL`, `lstat` ثم فتح — **لا يُزال السباق كليًا من فضاء المستخدم** | واجهة `_initialize` كلها | لا يُختبر آليًا (سباق تعشوائي) | PARTIALLY_PROVEN |
| T12 | تسرب خصوصية: تخزين سجلات خام أو مسارات أو نصوص عشوائية | قوائم مسموحة لكل حقل + قيود `CHECK` في SQLite كخط دفاع أخير | `_prepare_record`, مخطط الجدول | `test_rejects_raw_log_argument`, `test_rejects_path_in_version`, `test_rejects_arbitrary_category`, `test_extra_fields_are_rejected` | PROVEN |
| T13 | كتابة جزئية للدفعة عند فشل سجل في منتصفها | التحقق الكامل قبل المعاملة، ثم إدراج ذري بتراجع كامل | `add_records_atomic` | `test_invalid_second_record_rejects_entire_batch`, `test_database_failure_rolls_back_entire_batch` | PROVEN |
| T14 | ترحيل مخطط متعثّر أو متلاعب به (v1→v2) يترك القاعدة في حالة مكسورة | التحقق من بصمة SQL القانونية لـ v1 ومجموعة الأعمدة قبل المساس، ثم ترقية داخل معاملة واحدة `BEGIN IMMEDIATE`؛ أي فشل يتراجع ويُبقي القاعدة على v1 دون تعديل | `_migrate_v1_to_v2`, `_initialize` | `test_v1_database_migrates_and_preserves_rows`, `test_migration_failure_is_atomic`, `test_tampered_v1_sql_is_refused_without_migration`, `test_v1_with_extra_column_is_refused` | PROVEN |

## المخاطر المتبقية المعلنة (لا تُخفى ولا تُبالغ فيها)

هذه القيود مأخوذة نصًّا من توثيق الوحدة نفسه (`store.py`) وهي مقصودة
كنقاط للمراجعة المستقبلية:

1. مكوّنات المسار الأعلى من المجلد الأب المباشر لا تُفحص ضد الروابط
   الرمزية (فحصُها الكامل يتطلب المرور على كل مكوّنات المسار).
2. سباقات TOCTOU لا تُغلق تمامًا من فضاء المستخدم؛ يبقى التخفيف فقط.
3. ملفات الـjournal العابرة أثناء التشغيل تُنشأ بأذونات مشتقة من
   `umask` للتطبيق المضيف؛ على الأنظمة متعددة المستخدمين شغّل التطبيق
   بـ`umask 077`.
4. التهيئة تغيّر `umask` العملية مؤقتًا؛ أنشئ المخازن قبل الاعتماد على
   إنشاء ملفات متزامن في خيوط أخرى.

## مصادر هذا النموذج

- مراجعة أمنية خارجية (محادثة DeepSeek المشتركة، إجابة «الحزمة
  الهندسية») التي حددت بندًا ببندًا: أذونات المجلد والأثر المحدود لـ
  `exist_ok`، هجمات الروابط الرمزية للملف والأب وحدود `O_NOFOLLOW` أمام
  TOCTOU، فحص نوع الملف، أذونات ملفات WAL/SHM/journal المشتقة من
  `umask`، فجوة التحقق من المخطط الفعلي مقابل `user_version` وحده،
  وحماية التهيئة الجزئية.
- المرحلة 6 من خطة التطوير في المحادثة نفسها: قواعد الترحيل الآمن
  (فحص قبل المساس، معاملة ذرية، فشل يُبقي الإصدار القديم سليمًا) والتحذير
  بعدم رفع رقم الإصدار قبل اختبار الترحيل على قاعدة v1 حقيقية.
- التدقيق الداخلي الموثق في [SECURITY_AUDIT.md](SECURITY_AUDIT.md).
- سياسة عمل المساهمين والوكلاء في [AGENT_POLICY.md](AGENT_POLICY.md).

أي تغيير مستقبلي على طبقة التخزين يجب أن يحدّث هذا الجدول ويضيف
اختبارًا مثبِّتًا لكل تحصين جديد.

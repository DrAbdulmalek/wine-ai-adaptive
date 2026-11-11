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
| T4 | مجلد أب بأذونات فضفاضة أو منحرفة (0500 مثلًا) | فرض الوضع الدقيق `0700` وإصلاح الأذونات الموجودة قبل الفتح | `__init__`, `_verify_parent_directory` | `test_tightens_insecure_parent_directory_permissions`, `test_parent_directory_mode_is_enforced_to_exactly_0700` | PROVEN |
| T5 | ملف أو مجلد مملوك لمستخدم آخر | رفض صريح بمقارنة `st_uid` مع `geteuid` | `_verify_parent_directory`, `_verify_database_file`, `_verify_sidecar` | لا يُختبر آليًا (يتطلب صلاحيات root) | PARTIALLY_PROVEN |
| T6 | انحراف أذونات ملف القاعدة عن `0600` | إنشاء بـ`O_CREAT \| O_EXCL` بوضع `0600` + فرض `0600` على أي ملف موجود **قبل** فتح SQLite ثم إعادة الفرض بعد التهيئة | `_create_database_file`, `_verify_database_file`, `_restrict_file_permissions` | `test_existing_database_file_is_tightened_before_use`, `test_database_permissions_are_restricted` | PROVEN |
| T7 | قاعدة بيانات بختم إصدار مجهول أو مستقبلي | رفض أي إصدار خارج `{0, 1, 2, SCHEMA_VERSION}` | `_initialize` | `test_rejects_unknown_schema_version` | PROVEN |
| T8 | قاعدة بإصدار صحيح لكن بمخطط فاسد (أعمدة ناقصة/زائدة، قيود `CHECK` محذوفة) | مطابقة بصمة المخطط **لكل إصدار**: نص `CREATE` المخزّن حسب الإصدار المختوم + بيانات `PRAGMA table_info` | `_verify_schema` | `test_rejects_schema_without_check_constraints`, `test_rejects_schema_with_extra_column`, `test_rejects_schema_with_missing_column` | PROVEN |
| T9 | تهيئة جزئية (جدول بلا ختم إصدار أو ختم بلا جدول) نتيجة انهيار أثناء الإنشاء | كشف الحالة الجزئية ورفضها، والتهيئة داخل معاملة واحدة `BEGIN IMMEDIATE` مع تراجع كامل | `_initialize` | `test_rejects_partial_initialization`, `test_rejects_versioned_database_without_table` | PROVEN |
| T10 | ملفات جانبية `-wal/-shm/-journal` بترويسة رابط رمزي أو بأذونات فضفاضة تسرب بيانات | فحص كل مسار جانبي: رفض الروابط الرمزية وغير الاعتيادي والملكية الغريبة، وفرض `0600` على الموجود **قبل** فتح SQLite | `_verify_sidecar`, `_restrict_file_permissions` | `test_rejects_symlinked_sidecar_files`, `test_stale_sidecar_files_are_cleaned_or_restricted`, `test_wal_mode_database_remains_usable` | PROVEN |
| T11 | سباقات check-then-use (TOCTOU) بين الفحص والفتح | تضييق النافذة: `O_NOFOLLOW`, `O_EXCL`, `lstat` ثم فتح، وفحوص الترحيل داخل معاملة الكتابة نفسها — **لا يُزال السباق كليًا من فضاء المستخدم** | واجهة `_initialize` كلها | لا يُختبر آليًا (سباق تعشوائي) | PARTIALLY_PROVEN |
| T12 | تسرب خصوصية: تخزين سجلات خام أو مسارات أو نصوص عشوائية | قوائم مسموحة لكل حقل + قيود `CHECK` في SQLite تغطي **كل** الحقول المنظمة (v3: `wine_version`, `distro`, `error_code`, `prefix_id` أيضًا) كخط دفاع أخير + إعادة تحقق كل صف عند التصدير | `_prepare_record`, مخطط v3, `validate_existing_record` | `test_rejects_raw_log_argument`, `test_rejects_path_in_version`, `test_rejects_arbitrary_category`, `test_extra_fields_are_rejected`, `test_sqlite_engine_rejects_invalid_labels_without_python_api`, `test_export_blocked_when_tampered_row_detected` | PROVEN |
| T13 | كتابة جزئية للدفعة عند فشل سجل في منتصفها | التحقق الكامل قبل المعاملة، ثم إدراج ذري بتراجع كامل | `add_records_atomic` | `test_invalid_second_record_rejects_entire_batch`, `test_database_failure_rolls_back_entire_batch` | PROVEN |
| T14 | ترحيل مخطط متعثّر أو متلاعب به (سلسلة v1→v2→v3) يترك القاعدة في حالة مكسورة | سلسلة ترحيل ذرّية: فحوص البصمة والأعمدة **داخل** معاملة `BEGIN IMMEDIATE` نفسها (بلا نافذة تحقق-ثم-نفّذ)؛ v2→v3 إعادة بناء جدول ترفض أي صف يخالف قيود v3 وتتراجع كليًا | `_migrate_v1_to_v2`, `_migrate_v2_to_v3`, `_initialize` | `test_v1_database_migrates_and_preserves_rows`, `test_v2_database_migrates_to_v3_preserving_rows_and_ids`, `test_v2_to_v3_migration_rejects_poisoned_rows_atomically`, `test_tampered_v1_sql_is_refused_without_migration`, `test_v1_with_extra_column_is_refused` | PROVEN |

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

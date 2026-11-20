# Residual TOCTOU Risk Acceptance & Test Coverage Plan — قبول المخاطر المتبقية لطبقة اكتشاف Wine Prefix

**Project:** DrAbdulmalek/wine-ai-adaptive
**Pull Request الأصلي:** #5 — Security Hardening of Wine Prefix Discovery
**الفرع الأصلي:** `security/p1-bottle-discovery-hardening` (رأس المراجعة: `f2efb471b28f41a9db9bc11425e3645d5632e9db`)
**تاريخ التدقيق المستقل:** 2026-11-11
**إصدار الوثيقة:** 1.1 (FINAL DRAFT كما اعتمده المدقق المستقل)
**نطاق الوثيقة:** Read-only Wine Prefix Discovery فقط

---

## المصدر والإثبات (Provenance)

- وثّقت هذه الوثيقة نتيجة **تدقيق أمني وهندسي مستقل** انتهى إلى الحكم
  **CONDITIONALLY APPROVED** لمرحلة المراجعة التالية، مع خطة إصلاح
  مصنفة P0–P3.
- المدقق المستقل راجع الإصدار 1.1 من هذه الوثيقة وخلص إلى:
  «Version 1.1 correctly incorporates the material recommendations …
  The document is ready for formal maintainer sign-off in its current
  form.» — مع تأكيد أن الاعتماد لا يمنح دمج PR #5، ولا يفوّض عمليات
  كتابة، ولا يغلق المخاطرة المتبقية.
- أُحيلت الوثيقة إلى المستودع بتاريخ **2026-11-17** بتعليمة صريحة من
  مالك المشروع بتطبيق مخرجات تلك المحادثة كاملة — وهو ما يسجل توقيع
  المسؤول (Maintainer Sign-off) المعلّق الوارد في قسم Approval Record
  أدناه، **داخل نطاق القراءة فقط حصراً**.
- برهان التكامل (2026-11-17): مسار P1-B وP1-C وP1-D موجود في `main`
  فعلياً — `tests/unit/test_bottle_discovery_security.py` يغطي
  `EACCES/EPERM/ELOOP/ENOTDIR/ENOENT/ESTALE/EIO/EMFILE/ENFILE`، المسارات
  العميقة (سلسلة 48 مكوناً)، وسياسة Unknown-ليس-False، وسلامة أولوية
  الهوية المحمية؛ و`src/wine_ai/bottle_manager/discovery.py` يحمل
  `BLOCKED` عند تعذر التحقق من الهوية. فروع PRs #6/#7/#8 أُغلقت بلا دمج
  مباشر ومحتواها وصل عبر التكامل اللاحق (PR #17) — المصير موثق في
  EXTERNAL_REVIEWS.md وINDEPENDENT_SECURITY_AUDIT_2026-11-12.md.

---

## Purpose — الغرض

يوثق هذا المستند المخاطر المتبقية المتعلقة بسباقات التوقيت (TOCTOU) في
طبقة اكتشاف Wine Prefix، ويحدد خطة تحسين الاختبارات ومعايير قبول
المخاطر.

يستند إلى تدقيق أمني مستقل انتهى إلى: **CONDITIONALLY APPROVED**.

هذا المستند لا يمثل موافقة على الدمج، ولا تفويضاً لتطوير عمليات الكتابة.

## Security Boundary — حدود الأمان

تظل طبقة الاكتشاف ملتزمة بالمبادئ التالية:

- Read-Only by Default.
- No Wine Execution.
- No Prefix Modification.
- No Automatic Repair.
- No Arbitrary Shell Execution.
- Fail-Closed on Untrusted Filesystem Conditions.
- No Cloud Fallback.
- No Claims of Absolute Race Freedom.

تُستخدم نتائج الاكتشاف لأغراض التشخيص فقط، ولا تمنح أي صلاحية ضمنية
لعمليات الإنشاء أو الاستنساخ أو النسخ الاحتياطي أو الاستعادة أو الحذف.

## Residual Risk SEC-01 — TOCTOU

### 3.1 Existing Mitigations

تستخدم الشيفرة الحالية آليات الحماية التالية:

- Directory file descriptors.
- `O_DIRECTORY | O_NOFOLLOW`.
- Component-by-component path traversal.
- Device/inode identity comparison.
- Post-open `fstat()` validation.
- Non-blocking registry file access.
- Strict marker file type validation.

تقلل هذه الإجراءات مخاطر استبدال المجلدات والمكونات أثناء الفحص، لكنها
لا تلغي جميع المخاطر أمام خصم يمتلك صلاحيات متقدمة للتحكم بنقاط الربط
أو بيئة الملفات أو تنفيذ عمليات متزامنة على النظام.

### 3.2 Accepted Technical Limitation

يجب اعتماد النص التالي كما هو، دون تقويته أو استبداله بادعاء مطلق:

> «The implementation applies practical descriptor-based filesystem
> protections and identity validation to reduce TOCTOU exposure. Residual
> races involving privileged concurrent filesystem or mount manipulation
> cannot be fully eliminated by this user-space discovery implementation.»

يُمنع وصف التنفيذ بأنه:

- Race-free.
- TOCTOU-proof.
- Immune to privileged filesystem manipulation.
- Fully secure against mount namespace attacks.

### 3.3 Required Behavior

عند اكتشاف عدم اتساق الهوية أو نوع الملف أو فشل التحقق:

- رفض متابعة الفحص المتأثر.
- عدم تحويل الحالة غير الموثوقة إلى نتيجة سلبية مؤكدة.
- استخدام `None` أو حالة Unknown عند تعذر إثبات النتيجة.
- عدم إجراء أي محاولة إصلاح تلقائي.
- عدم تعديل الملفات أو المجلدات.

Status: **ACCEPTED RESIDUAL RISK (read-only scope)** — سجل توقيع المسؤول
في قسم Approval Record.

## Test Coverage Plan

### P1-A — Concurrent Directory Replacement

**Classification: OPTIONAL / BEST-EFFORT — منفذة الآن** (2026-11-17).

الهدف: جمع أدلة إضافية حول سلوك الفشل الآمن في ظل التغييرات المتزامنة.

التنفيذ في المستودع: `tests/unit/test_bottle_discovery_race_stress.py`
— عامل يكرر بناء/تسجيل/استبدال/حذف Prefixes كاملة عبر سلسلة rename في
مجلد اختبار مؤقت، وعامل آخر ينفذ الاكتشاف المتزامن، مع معيار القبول:
كل نتيجة إما تطابق هوية مسجلة كاملة أو فشلت مغلقة (مسار اختفى أثناء
الفحص) — ولا يُقبل أي نجاح صامت يشير إلى مجلد غير متوقع.

ضوابط الاختبار (محفوظة حرفياً):

- لا استخدام لمسارات المستخدم الحقيقية.
- لا استخدام لـ `~/.wine`.
- لا استخدام لـ Wine.
- لا استخدام لـ sudo.
- لا عمليات mount أو bind mount.
- لا اعتماد على توقيت ثابت أو `sleep()` باعتباره دليلاً أمنياً.
- لا اعتبار نجاح الاختبار إثباتاً لانعدام TOCTOU.

Important limitation: هذا الاختبار غير حتمي بطبيعته؛ إذ تعتمد نتيجة
السباق الحقيقي على التوقيت وجدولة العمليات وظروف نظام الملفات. لذلك:

- لا يُعامل كاختبار إثبات رياضي.
- لا يُرفع إلى بوابة إلزامية للدمج (لا يعمل في CI إلا عند ضبط
  `WINE_AI_RACE_STRESS=1`).
- لا يؤدي فشله وحده إلى إثبات وجود ثغرة.
- لا يؤدي نجاحه إلى إثبات انعدام الثغرات.
- يبقى اختباراً استكشافياً يقدم أدلة رصدية إضافية.

### P1-B — OSError Error-Path Coverage

**Classification: IMPORTANT — منفذة ومدمجة في main** (موثقة أعلاه).

توسيع تغطية مسارات الخطأ في `os.open` / `os.stat` / `os.fstat` /
`os.scandir` / `os.dup`:

| الخطأ | السلوك المتوقع |
|---|---|
| EACCES | Fail closed |
| EPERM | Fail closed |
| ELOOP | Reject untrusted path |
| ENOTDIR | Reject invalid component |
| ENOENT | Report unavailable |
| ESTALE | Fail closed / unknown |
| EIO | Fail closed |
| EMFILE | Fail safely without descriptor leakage |
| ENFILE | Fail safely without descriptor leakage |

التحقق المطلوب — إغلاق جميع file descriptors المفتوحة، عدم تسريب
الاستثناءات بتفاصيل غير لازمة، عدم تحويل الأخطاء إلى نتائج نجاح، عدم
تغيير حالة الملفات — مطبق ومختبر في مجموعة الاختبارات الحالية.

### P1-C — Deep Path and Permission Tests

**Classification: IMPORTANT — منفذة ومدمجة في main** (موثقة أعلاه).

تغطية: سلاسل مكونات عميقة (48 مكوناً)، مكونات وسيطة غير قابلة للقراءة،
مجلدات غير قابلة للعبور، مكونات تتغير أثناء الفحص، مسارات غير مكتملة.

السياسة المطبقة: «Incomplete, inaccessible, or untrusted intermediate
components must result in an Unknown state, not a confirmed absence.»

### P1-D — Protected Identity Precedence

**Classification: IMPORTANT / LOW-COST — منفذة ومدمجة في main** (موثقة
أعلاه).

اختبار تعاقدي يجمع مصادر اكتشاف متعددة للهوية نفسها (المسار الافتراضي،
`WINEPREFIX`، المسار المطبّع، المسار الرمزي إلى الهدف نفسه، جذر الحاوية)
ويتحقق من: عدم إصدار أكثر من نتيجة للهوية نفسها، وعدم انخفاض
`protected=True` مهما تعددت المصادر.

> «Once an identity is classified as protected, subsequent discovery
> origins must never downgrade its protection status.»

### 4.1 Existing Mock Tests vs. Real Concurrent Tests

يجب التمييز بوضوح بين نوعي الأدلة:

- **Mock-injected race tests**: تثبت أن الشيفرة تستجيب بصورة صحيحة
  لسيناريوهات السباق المحقونة حتمياً، وأن فحوص الهوية ترفض الحالات غير
  المتوقعة. لكنها لا تثبت غياب نافذة سباق حقيقية في ظروف التشغيل
  المتزامن.
- **Real concurrent stress tests** (P1-A): تقدم ملاحظات وأدلة رصدية
  إضافية حول سلوك التنفيذ تحت التزامن الفعلي، لكنها لا تثبت رياضياً
  انعدام TOCTOU ولا تحل محل تحليل التصميم أو اختبارات الحقن الحتمية.

## Documentation Requirements

قبل أي دمج مستقبلي، يجب مطابقة: HEAD SHA الفعلي، Base SHA الفعلي، PR
head/base branches، CI run ID، الالتزام الذي اختبره CI، نتائج إصدارات
Python. لا يجوز اعتبار نجاح CI على التزام سابق دليلاً على التزام أحدث.

ويجب أن تميز الوثائق بين: Verified implementation behavior / Mock-injected
race behavior / Real concurrent stress-test observations / Residual risks
that remain unproven or cannot be eliminated.

## Risk Acceptance Matrix

| Risk / Test | Severity | Status | Required action |
|---|---|---|---|
| SEC-01: Residual TOCTOU | Medium | **ACCEPTED (read-only scope)** | لا شيء إضافي — موثق وموقّع |
| SEC-02: Symlink identity handling | Low–Medium | Accepted design | No code change |
| SEC-03: Scan bound | Low | Proven | No action |
| SEC-04: FIFO and marker validation | Low | Proven | No action |
| P1-A: Concurrent race tests | Improvement | **Done (optional/best-effort)** | تشغيل اختياري عند الحاجة |
| P1-B: OSError coverage | P1 | **Done — في main** | مكتمل |
| P1-C: Deep path tests | P1 | **Done — في main** | مكتمل |
| P1-D: Protection precedence | P2 | **Done — في main** | مكتمل |

## Restrictions Before P2 Write-Capable Development

لا يُسمح باعتبار طبقة الاكتشاف وحدها أساساً كافياً لتفعيل:

- Create Bottle.
- Clone Bottle.
- Backup Bottle.
- Restore Bottle.
- Delete Bottle.
- Prefix Migration.
- Automated Repair.

كل عملية كتابة تحتاج إلى مراجعة مستقلة تشمل:

- Threat model خاص بالعملية.
- Path validation قبل الكتابة.
- Symlink and TOCTOU protection.
- Atomicity and rollback design.
- Backup and recovery verification.
- Independent approval gates.
- Tests تثبت عدم المساس بالمسارات خارج النطاق.
- موافقة صريحة على الانتقال من Read-Only إلى Write-Capable.

## Approval Record

**Maintainer sign-off — مسجل 2026-11-17:**

قرار المسؤول المنفذ: **"ACCEPT THE DOCUMENTED RESIDUAL RISK FOR
READ-ONLY DISCOVERY ONLY, SUBJECT TO MAINTAINER SIGN-OFF AND COMPLETION
OF THE AGREED TEST COVERAGE IMPROVEMENTS."**

تم تنفيذ شرطا القرار: التوقيع أعلاه (بتعليمة المالك بتطبيق مخرجات
التدقيق)، وإكمال تحسينات التغطية المتفق عليها (P1-B/C/D مدمجة في main،
وP1-A منفذة كاختبار اختياري).

هذا القبول:

- لا يمنح إذن الدمج لأي فرع قديم (PRs #5/#6/#7/#8 أُغلقت ومحتواها وصل
  عبر التكامل الموثق).
- لا يمنح إذن تطوير عمليات كتابة — **Write-Capable Operations: BLOCKED**.
- لا يغلق المخاطر المتبقية.
- لا يعني إثبات انعدام TOCTOU.
- يسجل فقط أن المخاطر مفهومة ومحددة ضمن نطاق الاستخدام التشخيصي
  للقراءة فقط.

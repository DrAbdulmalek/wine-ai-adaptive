# الإلهامات من المشاريع مفتوحة المصدر — wine-ai-adaptive

هذا الملف يوثّق أنماط التصميم التي استُلهمت من مشاريع مفتوحة المصدر أثناء بناء
النواة التشخيصية، ومكان ظهور كل نمط في الكود الفعلي.

**قاعدة الترخيص:** ما نأخذه من هذه المشاريع هو **تصميم أو نمط عمل**، لا كودًا
منسوخًا. كل الكود في هذا المستودع مكتوب من الصفر فوق مكتبة Python القياسية
فقط، والمشروع يبقى MIT. لا اعتماديات خارجية إلزامية ولا اختيارية.

| المشروع | النمط المستلهم | مكانه في هذا المستودع |
|---|---|---|
| **protontool** | استخراج بنيوي لأخطاء Wine/Proton من السجلات دون تخزين نص خام | `src/wine_ai/diagnostics/codes.py` — أنماط `HRESULT_RE` / `NTSTATUS_RE` / `WIN32_RE` وتصنيف إرشادي بثقة معلنة |
| **TheInkReaper/LogAnalyzer** | كشف أشكال `0x8...` (HRESULT) و`0xC...`/`0xE...` (NTSTATUS/استثناءات) والتمييز بينها وبين عناوين العودة العادية | `src/wine_ai/diagnostics/codes.py` — `_ERROR_SHAPE_PREFIXES` (فلترة الضجيج السداسي عشري) |
| **unxed/fix-it-in-wine + unxed/dllmgr** | سير «شخّص ← صنّف ← اقترح» بلا إصلاح تلقائي أبدًا | `src/wine_ai/knowledge/bridge.py` (`persist_findings`) و`src/wine_ai/cli.py` (أمر `diagnose` — مخرجاته اقتراحات/تصنيفات نصية فقط، لا ينفّذ أي أمر ولا يلمس Wine) |
| **Matoking/protontricks + Prefixer** | كشف المشغّل من أسماء متغيرات البيئة فقط، وتقليل البادئة إلى معرّف مجرد | `src/wine_ai/environment/snapshot.py` — `LAUNCHER_MARKERS` (Steam/Lutris/Bottles/Heroic) و`prefix_name` المجرد |
| **maci0/mcp-winedbg** | تغليف أدوات التشخيص لوكيل LLM محلي بلا سحابة | `src/wine_ai/providers/ollama/client.py` — عنوان ثابت `127.0.0.1` فقط، لا استدعاء شبكي خارجي |
| **WineHQ AppDB** | شكل بيانات التوافق (كائن، إصدار، تقييم) كبيانات مرفقة دون اتصال | `src/wine_ai/gpu_compatibility/data/vendor_map.json` + `vendor_data.py` (معرفة ثابتة bundled) |
| **petamas/python-ntstatus** | فك رموز NTSTATUS بشكل موثوق | **غير مُدمج** — يتطلب اعتمادية خارجية يمنعها نموذج الأمن (مكتبة قياسية فقط). جداول التصنيف الداخلية في `diagnostics/codes.py` تغطي الرموز الشائعة، والقرار موثّق في `docs/reviews/EXTERNAL_REVIEWS.md` |
| **GPU vendorID في `vulkaninfo`** (نمط شائع في أدوات Proton) | ربط `vendorID = 0x8086/0x1002/0x10DE` بمورّد GPU بلا استدعاءات إضافية | `src/wine_ai/gpu_compatibility/probe.py` — `parse_vulkan_summary` و`VENDOR_ID_MAP` |

## حدود الاستخدام

- لا يُنسخ أي كود من المشاريع أعلاه؛ المقارنة تصميمية (سير العمل، الأنماط،
  أشكال البيانات).
- كل إضافة مستلهمة تمر بنفس قواعد المشروع: قوائم مسموحة، لا نص خام، لا شبكة،
  لا تنفيذ أوامر، وتوثيق بتصنيف أدلة.
- أي اقتراح مستقبلي لإدماج اعتمادية خارجية (مثل `ntstatus`) يمر عبر تعديل
  موثّق في `docs/AGENT_POLICY.md` وقرار صريح من المالك قبل التنفيذ.

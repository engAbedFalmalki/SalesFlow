"""Single source of truth for metric definitions, grains and update rules.
Served by /api/definitions and rendered on the Architecture & definitions page."""

METRICS = [
    {"key": "gross_merchandise_value", "name": "Gross merchandise value (GMV)", "unit": "SAR", "date_basis": "order date",
     "definition": "Σ quantity × unit price recorded on the order line, before line discounts. Includes cancelled orders.",
     "sql": "SUM(FactSalesLine.GrossAmount)"},
    {"key": "ordered_value", "name": "Ordered value", "unit": "SAR", "date_basis": "order date",
     "definition": "Gross merchandise value minus line discounts. Includes cancelled orders.",
     "sql": "SUM(FactSalesLine.NetAmount)"},
    {"key": "active_ordered_value", "name": "Active ordered value", "unit": "SAR", "date_basis": "order date",
     "definition": "Ordered value excluding orders whose current status is cancelled.",
     "sql": "SUM(NetAmount) WHERE DimOrder.IsCancelled = 0"},
    {"key": "distinct_active_orders", "name": "Orders (non-cancelled)", "unit": "count", "date_basis": "order date",
     "definition": "Distinct orders with at least one accepted line whose current status is not cancelled.",
     "sql": "COUNT(DISTINCT OrderKey) WHERE IsCancelled = 0"},
    {"key": "average_order_value", "name": "Average order value (AOV)", "unit": "SAR", "date_basis": "order date",
     "definition": "Active ordered value ÷ distinct non-cancelled orders. Shown as n/a when there are no orders.",
     "sql": "active_ordered_value / NULLIF(distinct_active_orders, 0)"},
    {"key": "successful_collections", "name": "Successful collections", "unit": "SAR", "date_basis": "transaction date",
     "definition": "Σ amount of payment transactions with status succeeded. Failed and pending attempts are excluded.",
     "sql": "SUM(Amount) WHERE TransactionType = 'payment' AND TransactionStatus = 'succeeded'"},
    {"key": "successful_refunds", "name": "Successful refunds", "unit": "SAR", "date_basis": "transaction date",
     "definition": "Σ amount of refund transactions with status succeeded (amounts are stored as positive values).",
     "sql": "SUM(Amount) WHERE TransactionType = 'refund' AND TransactionStatus = 'succeeded'"},
    {"key": "net_collections", "name": "Net collections", "unit": "SAR", "date_basis": "transaction date",
     "definition": "Successful collections minus successful refunds. A cash-movement measure, not recognised revenue or profit.",
     "sql": "SUM(FactPaymentTransaction.NetCollectionAmount)"},
]

GRAINS = [
    {"table": "dw.DimDate", "grain": "One row per Asia/Riyadh calendar date (2025-01-01 to 2027-12-31).",
     "key": "DateKey (yyyymmdd)", "updates": "Static, created by migration."},
    {"table": "dw.DimCustomer", "grain": "One row per source customer (customer_id).", "key": "CustomerKey; unique CustomerId",
     "updates": "Type 1: name, email and city are overwritten with the latest accepted values. Region is derived from the city."},
    {"table": "dw.DimProduct", "grain": "One row per product SKU.", "key": "ProductKey; unique ProductSku",
     "updates": "Type 1: name, category and current list price are overwritten. Historical reporting by category therefore "
                "uses the latest category. Sales amounts never use the current list price."},
    {"table": "dw.DimOrder", "grain": "One row per source order (order header).", "key": "OrderKey; unique OrderId",
     "updates": "Type 1: status overwritten. Metrics use the current status (an order cancelled later is excluded from "
                "active ordered value on its original order date)."},
    {"table": "dw.FactSalesLine", "grain": "One row per source order line (order_item_id).",
     "key": "SalesLineKey; unique OrderItemId",
     "updates": "Upserted when the source line or its order header changes. UnitPrice is the price recorded on the line."},
    {"table": "dw.FactPaymentTransaction", "grain": "One row per source payment or refund transaction (transaction_id).",
     "key": "PaymentTxnKey; unique TransactionId",
     "updates": "Upserted when a later file delivers a changed version of the same transaction id."},
]

DATE_RULES = [
    "Timestamps are stored in UTC (columns ending in Utc).",
    "Business dates and all date filters use Asia/Riyadh (UTC+3, no daylight saving).",
    "Sales metrics are filtered by the order date. Collections and refunds are filtered by the transaction date.",
    "A payment received on 2 Sep for an order placed on 31 Aug counts in August sales and in September collections.",
    "Period comparisons use the immediately preceding window of equal length.",
]

EXCLUSIONS = [
    "Tax, shipping fees and multi-currency accounting are out of scope; every amount is SAR.",
    "Order-level payments and refunds are not allocated to products. Product analytics use sales-line measures only.",
    "Collections are cash movements, not recognised accounting revenue or profit.",
    "Sales-line and payment facts are never joined row-to-row; each is aggregated to order or date grain first.",
]

LIMITATIONS = [
    "Type 1 dimensions only; no history of category or customer attribute changes (Type 2 is a future extension).",
    "Hard deletes in the operational database are not detected; status changes (e.g. cancelled) are.",
    "A newer source version that fails validation leaves the last accepted version in the warehouse until corrected.",
    "Reconciliation and change detection load per-entity key sets into memory: designed for this dataset's scale "
    "(tens of thousands of rows), not hundreds of millions.",
    "A catalog snapshot older than the accepted one is ignored and re-evaluated on each run until a newer snapshot arrives.",
    "Single store, single currency, synthetic data only.",
]

# ---------------------------------------------------------------- Arabic (numbers and code stay as-is)

METRICS_AR = {
    "gross_merchandise_value": ("إجمالي قيمة البضائع (GMV)", "تاريخ الطلب",
                                "مجموع الكمية × سعر الوحدة المسجّل في سطر الطلب، قبل خصومات الأسطر. يشمل الطلبات الملغاة."),
    "ordered_value": ("قيمة الطلبات", "تاريخ الطلب", "إجمالي قيمة البضائع ناقص خصومات الأسطر. يشمل الطلبات الملغاة."),
    "active_ordered_value": ("قيمة الطلبات النشطة", "تاريخ الطلب", "قيمة الطلبات باستثناء الطلبات التي حالتها الحالية ملغاة."),
    "distinct_active_orders": ("الطلبات (غير الملغاة)", "تاريخ الطلب",
                               "عدد الطلبات المميزة التي فيها سطر مقبول واحد على الأقل وحالتها الحالية ليست ملغاة."),
    "average_order_value": ("متوسط قيمة الطلب (AOV)", "تاريخ الطلب",
                            "قيمة الطلبات النشطة ÷ عدد الطلبات غير الملغاة. يظهر «غير متاح» عندما لا توجد طلبات."),
    "successful_collections": ("التحصيلات الناجحة", "تاريخ العملية",
                               "مجموع مبالغ عمليات الدفع ذات الحالة «ناجحة». المحاولات الفاشلة والمعلّقة مستبعدة."),
    "successful_refunds": ("الاستردادات الناجحة", "تاريخ العملية",
                           "مجموع مبالغ عمليات الاسترداد ذات الحالة «ناجحة» (تُخزَّن المبالغ موجبة)."),
    "net_collections": ("صافي التحصيلات", "تاريخ العملية",
                        "التحصيلات الناجحة ناقص الاستردادات الناجحة. مقياس لحركة النقد، وليس إيراداً محاسبياً ولا ربحاً."),
}

GRAINS_AR = {
    "dw.DimDate": ("صف لكل تاريخ تقويمي بتوقيت الرياض (من 2025-01-01 إلى 2027-12-31).", "ثابت، يُنشأ عبر الترحيل."),
    "dw.DimCustomer": ("صف لكل عميل في المصدر (customer_id).", "النوع 1: الاسم والبريد والمدينة تُستبدل بآخر قيم مقبولة. المنطقة مشتقة من المدينة."),
    "dw.DimProduct": ("صف لكل رمز منتج (SKU).",
                      "النوع 1: الاسم والفئة وسعر القائمة الحالي تُستبدل. لذلك تستخدم التقارير التاريخية حسب الفئة آخر فئة. "
                      "مبالغ المبيعات لا تستخدم سعر القائمة الحالي أبداً."),
    "dw.DimOrder": ("صف لكل طلب في المصدر (رأس الطلب).",
                    "النوع 1: الحالة تُستبدل. المقاييس تستخدم الحالة الحالية (الطلب الذي يُلغى لاحقاً يُستبعد من قيمة الطلبات "
                    "النشطة في تاريخ طلبه الأصلي)."),
    "dw.FactSalesLine": ("صف لكل سطر طلب في المصدر (order_item_id).",
                         "يُحدَّث عند تغيّر سطر المصدر أو رأس طلبه. سعر الوحدة هو السعر المسجّل في السطر."),
    "dw.FactPaymentTransaction": ("صف لكل عملية دفع أو استرداد في المصدر (transaction_id).",
                                  "يُحدَّث عندما يصل ملف لاحق بنسخة معدّلة من نفس معرّف العملية."),
}

DATE_RULES_AR = [
    "تُخزَّن الأوقات بتوقيت UTC (الأعمدة المنتهية بـ Utc).",
    "تواريخ العمل وكل فلاتر التاريخ تستخدم توقيت الرياض (UTC+3، دون توقيت صيفي).",
    "مقاييس المبيعات تُفلتر حسب تاريخ الطلب، والتحصيلات والاستردادات حسب تاريخ العملية.",
    "دفعة استُلمت في 2 سبتمبر لطلب أُنشئ في 31 أغسطس تُحسب في مبيعات أغسطس وفي تحصيلات سبتمبر.",
    "المقارنة تكون مع الفترة السابقة مباشرة وبنفس الطول.",
]

EXCLUSIONS_AR = [
    "الضريبة ورسوم الشحن والعملات المتعددة خارج النطاق، وكل المبالغ بالريال السعودي (SAR).",
    "لا تُوزَّع المدفوعات والاستردادات على مستوى الطلب على المنتجات. تحليلات المنتجات تستخدم مقاييس أسطر المبيعات فقط.",
    "التحصيلات حركة نقد، وليست إيراداً محاسبياً معترفاً به ولا ربحاً.",
    "لا يُربط جدول أسطر المبيعات بجدول المدفوعات صفاً بصف، بل يُجمَّع كل منهما أولاً على مستوى الطلب أو التاريخ.",
]

LIMITATIONS_AR = [
    "أبعاد من النوع 1 فقط، دون تاريخ لتغيّر الفئة أو بيانات العميل (النوع 2 تطوير مستقبلي).",
    "الحذف الفعلي في قاعدة البيانات التشغيلية لا يُكتشف، أما تغيّر الحالة (مثل الإلغاء) فيُكتشف.",
    "إذا فشلت نسخة أحدث من سجل في التحقق، تبقى آخر نسخة مقبولة في المستودع حتى يُصحَّح المصدر.",
    "المطابقة واكتشاف التغيير يحمّلان مفاتيح كل كيان في الذاكرة، وهذا مناسب لحجم هذه البيانات (عشرات الآلاف من الصفوف) "
    "وليس لمئات الملايين.",
    "لقطة الكتالوج الأقدم من المقبولة تُتجاهل وتُعاد مراجعتها في كل تشغيل حتى تصل لقطة أحدث.",
    "متجر واحد، عملة واحدة، وبيانات اصطناعية فقط.",
]

NOTICE_AR = ("بيانات اصطناعية. كل عميل وطلب ومنتج ودفعة في هذه البيانات أُنشئ بواسطة salesflow.generator "
             "من بذرة عشوائية ثابتة. لا يوجد أشخاص أو معاملات حقيقية.")


def localized(lang: str) -> dict:
    """Definitions in the requested language. Codes, SQL and numbers are never translated."""
    if lang != "ar":
        return {"metrics": METRICS, "grains": GRAINS, "date_rules": DATE_RULES, "exclusions": EXCLUSIONS,
                "limitations": LIMITATIONS}
    metrics = [{**m, "name": METRICS_AR[m["key"]][0], "date_basis_label": METRICS_AR[m["key"]][1],
                "definition": METRICS_AR[m["key"]][2]} for m in METRICS]
    grains = [{**g, "grain": GRAINS_AR[g["table"]][0], "updates": GRAINS_AR[g["table"]][1]} for g in GRAINS]
    return {"metrics": metrics, "grains": grains, "date_rules": DATE_RULES_AR, "exclusions": EXCLUSIONS_AR,
            "limitations": LIMITATIONS_AR}

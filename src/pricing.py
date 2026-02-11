"""
Pricing module for Wheelseye GPS plans
Handles: Hindi number pronunciation, vehicle number formatting,
payload validation, and GST calculations.
"""

# ─── Hindi Number System (0-99 lookup) ────────────────────────────────────────

HINDI_NUMBERS = [
    "शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ",
    "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस",
    "बीस", "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस",
    "तीस", "इकतीस", "बत्तीस", "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस",
    "चालीस", "इकतालीस", "बयालीस", "तैंतालीस", "चवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास",
    "पचास", "इक्यावन", "बावन", "तिरपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ",
    "साठ", "इकसठ", "बासठ", "तिरसठ", "चौंसठ", "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ", "उनहत्तर",
    "सत्तर", "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उनासी",
    "अस्सी", "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सतासी", "अट्ठासी", "नवासी",
    "नब्बे", "इक्यानवे", "बानवे", "तिरानवे", "चौरानवे", "पचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे", "निन्यानवे",
]


def number_to_hindi(n: int) -> str:
    """Convert any integer to Hindi words.
    Examples: 2400 → 'दो हज़ार चार सौ', 0 → 'शून्य', 76 → 'छिहत्तर'
    """
    if n < 0:
        return "ऋण " + number_to_hindi(-n)
    if n < 100:
        return HINDI_NUMBERS[n]
    if n < 1000:
        hundreds = n // 100
        remainder = n % 100
        result = HINDI_NUMBERS[hundreds] + " सौ"
        if remainder > 0:
            result += " " + HINDI_NUMBERS[remainder]
        return result
    if n < 100000:
        thousands = n // 1000
        remainder = n % 1000
        result = number_to_hindi(thousands) + " हज़ार"
        if remainder > 0:
            result += " " + number_to_hindi(remainder)
        return result
    if n < 10000000:
        lakhs = n // 100000
        remainder = n % 100000
        result = number_to_hindi(lakhs) + " लाख"
        if remainder > 0:
            result += " " + number_to_hindi(remainder)
        return result
    return str(n)


# ─── Vehicle Number Handling ──────────────────────────────────────────────────

def extract_last_4_digits(vehicle_number: str) -> str:
    """Extract last 4 digits from vehicle number string."""
    digits = ''.join(c for c in vehicle_number if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits


def vehicle_last4_to_hindi(vehicle_number: str) -> str:
    """Convert last 4 digits to Hindi split format.
    Example: 'RJ29GB7437' → 'चौहत्तर सैंतीस' (74 + 37)
    Example: 'BR06GC0933' → 'शून्य नौ तैंतीस' (09 + 33)
    """
    last4 = extract_last_4_digits(vehicle_number)
    if len(last4) < 4:
        return last4
    pair1_str = last4[:2]
    pair2_str = last4[2:]
    pair1 = int(pair1_str)
    pair2 = int(pair2_str)

    # Handle leading zero: '09' → 'शून्य नौ', not just 'नौ'
    def pair_to_hindi(val, raw):
        if raw[0] == '0' and val < 10 and val > 0:
            return f"शून्य {number_to_hindi(val)}"
        return number_to_hindi(val)

    return f"{pair_to_hindi(pair1, pair1_str)} {pair_to_hindi(pair2, pair2_str)}"


# ─── Plan Constants ───────────────────────────────────────────────────────────

PLAN_KEYS = [
    "plan_1m", "plan_3m", "plan_3m_24d_limited", "plan_6m",
    "gps_1yr", "gps_1yr_tracking_only",
    "plan_1yr_gps_unlimited_login_dcm_ds",
    "gps_2yr", "gps_4yr",
]

PLAN_NAMES = {
    "plan_1m": "1 महीने का अनलिमिटेड GPS प्लान",
    "plan_3m": "3 महीने का अनलिमिटेड GPS प्लान",
    "plan_3m_24d_limited": "3 महीने का लिमिटेड GPS प्लान (24 दिन ट्रैकिंग)",
    "plan_6m": "6 महीने का GPS अनलिमिटेड प्लान",
    "gps_1yr": "1 साल का GPS प्लान",
    "gps_1yr_tracking_only": "1 साल का लाइट GPS प्लान (केवल ट्रैकिंग)",
    "plan_1yr_gps_unlimited_login_dcm_ds": "1 साल का प्रीमियम GPS प्लान (अनलिमिटेड लॉगिन + DCM + ड्राइवर स्कोर)",
    "gps_2yr": "2 साल का GPS प्लान",
    "gps_4yr": "4 साल का GPS प्लान",
}

PLAN_FEATURES = {
    "plan_1m": "लाइव लोकेशन, प्ले रूट, रूट हिस्ट्री, थेफ़्ट रिपोर्ट, रिले, नेविगेशन, जियोफेंसिंग, पार्किंग लॉक",
    "plan_3m": "लाइव लोकेशन, प्ले रूट, रूट हिस्ट्री, थेफ़्ट रिपोर्ट, रिले, नेविगेशन, जियोफेंसिंग, पार्किंग लॉक",
    "plan_3m_24d_limited": "लाइव लोकेशन, प्ले रूट, थेफ़्ट रिपोर्ट, रिले, नेविगेशन, जियोफेंसिंग, पार्किंग लॉक",
    "plan_6m": "लाइव लोकेशन, प्ले रूट, रूट हिस्ट्री, थेफ़्ट रिपोर्ट, रिले, नेविगेशन, जियोफेंसिंग, पार्किंग लॉक",
    "gps_1yr": "लाइव लोकेशन, प्ले रूट, रूट हिस्ट्री, थेफ़्ट रिपोर्ट, रिले, नेविगेशन, जियोफेंसिंग, पार्किंग लॉक",
    "gps_1yr_tracking_only": "केवल लाइव लोकेशन",
    "plan_1yr_gps_unlimited_login_dcm_ds": "सभी GPS फ़ीचर्स + अनलिमिटेड लॉगिन + DCM + ड्राइवर स्कोर",
    "gps_2yr": "लाइव लोकेशन, प्ले रूट, रूट हिस्ट्री, थेफ़्ट रिपोर्ट, रिले, नेविगेशन, जियोफेंसिंग, पार्किंग लॉक",
    "gps_4yr": "लाइव लोकेशन, प्ले रूट, रूट हिस्ट्री, थेफ़्ट रिपोर्ट, रिले, नेविगेशन, जियोफेंसिंग, पार्किंग लॉक",
}

PLAN_DURATIONS = {
    "plan_1m": "एक महीना",
    "plan_3m": "तीन महीने",
    "plan_3m_24d_limited": "तीन महीने (चौबीस दिन ट्रैकिंग)",
    "plan_6m": "छह महीने",
    "gps_1yr": "एक साल",
    "gps_1yr_tracking_only": "एक साल",
    "plan_1yr_gps_unlimited_login_dcm_ds": "एक साल",
    "gps_2yr": "दो साल",
    "gps_4yr": "चार साल",
}


# ─── Pricing Manager ──────────────────────────────────────────────────────────

class PricingManager:
    """Manages GPS plan pricing. Can be initialized from JSON payload or defaults."""

    GST_RATE = 0.18

    def __init__(self, payload: dict = None):
        self.plans = {}
        if payload:
            self.load_from_payload(payload)
        else:
            self._load_defaults()

    def _load_defaults(self):
        defaults = {
            "gps_1yr": 2000, "gps_1yr_tracking_only": None,
            "gps_2yr": 3300, "gps_4yr": 6000, "plan_6m": 1300,
            "plan_3m": 800, "plan_3m_24d_limited": 500,
            "plan_1m": None, "plan_1yr_gps_unlimited_login_dcm_ds": 2400,
        }
        for key, price in defaults.items():
            self.plans[key] = {"base_price": price, "name": PLAN_NAMES.get(key, key)}

    def load_from_payload(self, payload: dict):
        """Load plan prices directly from JSON payload."""
        for key in PLAN_KEYS:
            price = payload.get(key)
            self.plans[key] = {
                "base_price": price if price is not None else None,
                "name": PLAN_NAMES.get(key, key),
            }

    def calculate_gst(self, base_price):
        if base_price is None:
            return None
        return round(base_price * self.GST_RATE, 2)

    def calculate_final_price(self, base_price):
        if base_price is None:
            return None
        return round(base_price + self.calculate_gst(base_price), 2)

    def get_plan_details(self, plan_key: str) -> dict:
        if plan_key not in self.plans:
            return None
        plan = self.plans[plan_key]
        base_price = plan["base_price"]
        if base_price is None:
            return {"plan_name": plan["name"], "base_price": None, "gst": None,
                    "final_price": None, "available": False}
        return {
            "plan_name": plan["name"], "base_price": base_price,
            "gst": self.calculate_gst(base_price),
            "final_price": self.calculate_final_price(base_price),
            "available": True,
        }

    def get_all_plans_with_pricing(self) -> dict:
        return {key: self.get_plan_details(key) for key in self.plans}

    def format_price_hindi(self, price) -> str:
        """Format price as Hindi words for speech. 2400 → 'दो हज़ार चार सौ'"""
        if price is None:
            return "मूल्य उपलब्ध नहीं"
        return number_to_hindi(int(price))

    def build_plan_description(self, plan_key: str) -> str:
        """Build a concise plan description for speech."""
        details = self.get_plan_details(plan_key)
        if not details or not details["available"]:
            name = PLAN_NAMES.get(plan_key, plan_key)
            return f"{name}: यह प्लान अभी उपलब्ध नहीं है."
        name = PLAN_NAMES.get(plan_key, plan_key)
        duration = PLAN_DURATIONS.get(plan_key, "")
        features = PLAN_FEATURES.get(plan_key, "")
        base_h = self.format_price_hindi(details["base_price"])
        final_h = self.format_price_hindi(details["final_price"])
        return (
            f"• {name} — कीमत: {base_h} रुपये प्लस अठारह प्रतिशत GST, "
            f"कुल {final_h} रुपये; वैधता: {duration}. "
            f"फ़ीचर्स: {features}."
        )

    def update_plan_price(self, plan_key: str, base_price):
        if plan_key in self.plans:
            self.plans[plan_key]["base_price"] = base_price


# ─── Payload Validation ──────────────────────────────────────────────────────

def resolve_recommendation(recommendation: str) -> str:
    """Match a potentially truncated recommendation key to full plan key."""
    if not recommendation:
        return "gps_1yr"
    recommendation = recommendation.rstrip(".")
    if recommendation in PLAN_KEYS:
        return recommendation
    matches = [k for k in PLAN_KEYS if k.startswith(recommendation)]
    return matches[0] if len(matches) == 1 else "gps_1yr"


def validate_payload(payload: dict) -> dict:
    """Validate JSON payload and extract required fields with safe defaults."""
    v = {}
    v["operator_name"] = payload.get("operator_name", "ग्राहक")
    v["first_name"] = v["operator_name"].split()[0] if v["operator_name"] else "ग्राहक"
    v["vehicle_number"] = payload.get("vehicle_number", "")
    v["vehicle_last4_hindi"] = vehicle_last4_to_hindi(v["vehicle_number"]) if v["vehicle_number"] else ""
    v["recommendation"] = resolve_recommendation(payload.get("recommendation", "gps_1yr"))
    v["app_usage"] = str(payload.get("app_usage_before_renewal", ""))
    v["total_due_veh"] = payload.get("total_due_veh", 1)
    v["distance_covered"] = payload.get("distance_covered_before_renewal", 0)
    v["app_type"] = payload.get("app_type", "Android")
    v["autopay_active"] = bool(payload.get("autopay_active_flag", 0))
    v["phone_number"] = payload.get("phone_number", "")
    return v


# Initialize with defaults — will be re-initialized with payload in wheelseye.py
pricing_manager = PricingManager()

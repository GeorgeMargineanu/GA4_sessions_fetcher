import json
from typing import Tuple
import traceback

from google.analytics.admin import AnalyticsAdminServiceClient
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest,
    Dimension,
    Metric,
    DateRange,
    OrderBy,
    FilterExpression,
    FilterExpressionList,
    Filter,
)
from google.oauth2.credentials import Credentials


# ----------------------------
# CORS helpers
# ----------------------------
def _cors_headers():
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    }

def _handle_preflight(request):
    # Browsers send an OPTIONS preflight for requests with Authorization header.
    if request.method == "OPTIONS":
        return ("", 204, _cors_headers())
    return None

# ----------------------------
# Auth helper
# ----------------------------
def _get_user_credentials_from_request(request) -> Tuple[Credentials, str]:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ValueError("Missing/invalid Authorization header. Use: Bearer <ACCESS_TOKEN>")

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise ValueError("Empty bearer token.")

    # Important: pass scopes hint (helps some libs / debugging)
    creds = Credentials(
        token=token,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    return creds, token


# ============================
# FUNCTION 1: LIST ACCOUNTS + PROPERTIES
# ============================
def ga4_list_accounts_oauth(request):
    # CORS preflight
    if request.method == "OPTIONS":
        return ("", 204, _cors_headers())

    try:
        user_creds, _ = _get_user_credentials_from_request(request)

        client = AnalyticsAdminServiceClient(credentials=user_creds)
        accounts_data = []

        for summary in client.list_account_summaries():
            account_entry = {
                "account": summary.account,
                "displayName": summary.display_name,
                "properties": [],
            }

            for prop_summary in summary.property_summaries:
                prop_id = prop_summary.property.split("/")[-1]
                account_entry["properties"].append(
                    {
                        "property": prop_summary.property,
                        "propertyId": prop_id,
                        "displayName": prop_summary.display_name,
                    }
                )

            accounts_data.append(account_entry)

        return (json.dumps({"accounts": accounts_data}), 200, _cors_headers())

    except Exception as e:
        # Return full debug information to the caller (temporary, for testing)
        err = {
            "error": str(e),
            "traceback": traceback.format_exc(),
        }
        return (json.dumps(err), 500, _cors_headers())


# ============================
# FUNCTION 2: CONVERSIONS FOR A GIVEN PROPERTY
# ============================
def ga4_property_conversions_oauth(request):
    """
    HTTP Cloud Function that returns conversions for a given property id.

    Required:
      Header:
        Authorization: Bearer <ACCESS_TOKEN>  (analytics.readonly)
      Query/body:
        property_id (required): e.g. "182279779"
        start_date (optional): e.g. "30daysAgo" or "2025-12-01"
        end_date   (optional): e.g. "today" or "2025-12-15"
    """
    pre = _handle_preflight(request)
    if pre:
        return pre

    try:
        user_creds, _ = _get_user_credentials_from_request(request)
    except ValueError as e:
        return (json.dumps({"error": str(e)}), 401, _cors_headers())

    # --- parameters: query string or JSON body ---
    property_id = request.args.get("property_id") if request.args else None
    start_date = request.args.get("start_date") if request.args else None
    end_date = request.args.get("end_date") if request.args else None

    if not property_id:
        data = request.get_json(silent=True) or {}
        property_id = property_id or data.get("property_id")
        start_date = start_date or data.get("start_date")
        end_date = end_date or data.get("end_date")

    if not property_id:
        return (json.dumps({"error": "Missing required parameter: property_id"}), 400, _cors_headers())

    start_date = start_date or "30daysAgo"
    end_date = end_date or "today"

    # --- GA4 Data API call ---
    data_client = BetaAnalyticsDataClient(credentials=user_creds)

    request_body = RunReportRequest(
        property=f"properties/{property_id}",
        metrics=[{"name": "conversions"}], 
        date_ranges=[{"start_date": start_date, "end_date": end_date}],
    )

    response = data_client.run_report(request_body)

    total_conversions = 0
    if response.rows:
        total_conversions = int(response.rows[0].metric_values[0].value)

    result = {
        "propertyId": property_id,
        "dateRange": {"start_date": start_date, "end_date": end_date},
        "metrics": {"conversions": total_conversions},  
    }

    return (json.dumps(result), 200, _cors_headers())

def ga4_property_conversion_breakdown_oauth(request):
    """
    Returns:
      - total conversions
      - paid conversions (filtered)
      - conversion types (eventName -> conversions)
      - subchannels (sessionDefaultChannelGroup -> conversions + sessions + sessionConversionRate)
    """
    pre = _handle_preflight(request)
    if pre:
        return pre

    try:
        try:
            user_creds, _ = _get_user_credentials_from_request(request)
        except ValueError as e:
            return (json.dumps({"error": str(e)}), 401, _cors_headers())

        # --- parameters: query string or JSON body ---
        property_id = request.args.get("property_id") if request.args else None
        start_date  = request.args.get("start_date") if request.args else None
        end_date    = request.args.get("end_date") if request.args else None
        subchannel_dim = request.args.get("subchannel_dim") if request.args else None

        if not property_id:
            data = request.get_json(silent=True) or {}
            property_id = property_id or data.get("property_id")
            start_date  = start_date  or data.get("start_date")
            end_date    = end_date    or data.get("end_date")
            subchannel_dim = subchannel_dim or data.get("subchannel_dim")

        if not property_id:
            return (json.dumps({"error": "Missing required parameter: property_id"}), 400, _cors_headers())

        start_date = start_date or "30daysAgo"
        end_date   = end_date or "today"
        subchannel_dim = subchannel_dim or "sessionDefaultChannelGroup"

        data_client = BetaAnalyticsDataClient(credentials=user_creds)
        prop = f"properties/{property_id}"
        dr = [DateRange(start_date=start_date, end_date=end_date)]

        def run(req: RunReportRequest):
            return data_client.run_report(req)

        def safe_int(v):
            try:
                return int(float(v))
            except Exception:
                return 0

        def safe_float(v):
            try:
                return float(v)
            except Exception:
                return 0.0

        # -----------------------------
        # 1) TOTAL conversions (all)
        # -----------------------------
        total_req = RunReportRequest(
            property=prop,
            date_ranges=dr,
            metrics=[Metric(name="conversions")],
        )
        total_res = run(total_req)
        total_conversions = 0
        if total_res.rows:
            total_conversions = safe_int(total_res.rows[0].metric_values[0].value)

        # -----------------------------
        # 2) PAID conversions
        # FIX: include the dimension used in the filter,
        # and SUM conversions across returned rows
        # -----------------------------
        paid_filter = FilterExpression(
            or_group=FilterExpressionList(expressions=[
                # A) sessionDefaultChannelGroup starts with "Paid" (Paid Search, Paid Social, etc.)
                FilterExpression(
                    filter=Filter(
                        field_name="sessionDefaultChannelGroup",
                        string_filter=Filter.StringFilter(
                            match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                            value="(?i)^paid"
                        ),
                    )
                ),
                # B) sessionMedium is cpc / cpm / paid 
                FilterExpression(
                    filter=Filter(
                        field_name="sessionMedium",
                        string_filter=Filter.StringFilter(
                            match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                            value=r"(?i)^(cpc|cpm|ppc|paid)$"
                        ),
                    )
                ),
                # (Optional, but helpful) C) sessionSourceMedium contains /cpc or /cpm etc.
                FilterExpression(
                    filter=Filter(
                        field_name="sessionSourceMedium",
                        string_filter=Filter.StringFilter(
                            match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                            value=r"(?i)/(cpc|cpm|ppc|paid)$"
                        ),
                    )
                ),
            ])
        )

        paid_req = RunReportRequest(
            property=prop,
            date_ranges=dr,
            dimensions=[
                Dimension(name="sessionDefaultChannelGroup"),
                Dimension(name="sessionMedium"),
                Dimension(name="sessionSourceMedium"),
            ],
            metrics=[Metric(name="conversions")],
            dimension_filter=paid_filter,
            limit=1000,
        )

        paid_res = run(paid_req)
        paid_conversions = 0
        if paid_res.rows:
            for r in paid_res.rows:
                paid_conversions += safe_int(r.metric_values[0].value)

        # -----------------------------
        # 3) Conversion types (eventName)
        # -----------------------------
        types_req = RunReportRequest(
            property=prop,
            date_ranges=dr,
            dimensions=[Dimension(name="eventName")],
            metrics=[Metric(name="conversions")],
            order_bys=[OrderBy(metric={"metric_name": "conversions"}, desc=True)],
            limit=500,
        )
        types_res = run(types_req)

        conversion_types = []
        if types_res.rows:
            for r in types_res.rows:
                event_name = r.dimension_values[0].value
                convs = safe_int(r.metric_values[0].value)
                if convs > 0:
                    conversion_types.append({"eventName": event_name, "conversions": convs})

        # -----------------------------
        # 4) Subchannels breakdown
        # -----------------------------
        sub_req = RunReportRequest(
            property=prop,
            date_ranges=dr,
            dimensions=[Dimension(name=subchannel_dim)],
            metrics=[
                Metric(name="conversions"),
                Metric(name="sessions"),
                Metric(name="sessionConversionRate"),
            ],
            order_bys=[OrderBy(metric={"metric_name": "conversions"}, desc=True)],
            limit=200,
        )
        sub_res = run(sub_req)

        subchannels = []
        if sub_res.rows:
            for r in sub_res.rows:
                name = r.dimension_values[0].value
                convs = safe_int(r.metric_values[0].value)
                sessions = safe_int(r.metric_values[1].value)
                conv_rate = safe_float(r.metric_values[2].value)
                subchannels.append({
                    "subchannel": name,
                    "conversions": convs,
                    "sessions": sessions,
                    "conversionRate": conv_rate,
                })

        result = {
            "propertyId": property_id,
            "dateRange": {"start_date": start_date, "end_date": end_date},
            "totals": {
                "conversions": total_conversions,
                "paid_conversions": paid_conversions,
            },
            "conversionTypes": conversion_types,
            "subchannels": {
                "dimension": subchannel_dim,
                "rows": subchannels,
            },
        }

        return (json.dumps(result), 200, _cors_headers())

    except Exception as e:
        err = {
            "error": str(e),
            "traceback": traceback.format_exc(),
        }
        return (json.dumps(err), 500, _cors_headers())


def ga4_session_marketing_rows_oauth(request):
    """
    Returns raw session-level marketing breakdown rows.

    Required:
      Authorization: Bearer <ACCESS_TOKEN> (analytics.readonly)

    Params (query string or JSON body):
      property_id (required): e.g. "182279779"
      start_date (optional): default "30daysAgo"
      end_date   (optional): default "today"
      limit      (optional): default 10000
      offset     (optional): default 0
      dims       (optional): comma-separated GA4 dimensions to use instead of defaults
                             default:
                               sessionSourceMedium,sessionSource,sessionMedium,sessionCampaignName

    Response:
      {
        propertyId,
        dateRange,
        dims,
        limit,
        offset,
        rowCount,
        rows: [
          {
            "sessionSourceMedium": "...",
            "sessionSource": "...",
            "sessionMedium": "...",
            "sessionCampaignName": "...",
            "sessions": 123,
            "conversions": 4,
            "sessionConversionRate": 0.0123
          }, ...
        ]
      }
    """
    pre = _handle_preflight(request)
    if pre:
        return pre

    try:
        user_creds, _ = _get_user_credentials_from_request(request)
    except ValueError as e:
        return (json.dumps({"error": str(e)}), 401, _cors_headers())

    # ---- parameters: query string or JSON body ----
    args = request.args or {}
    data = request.get_json(silent=True) or {}

    property_id = args.get("property_id") or data.get("property_id")
    start_date  = args.get("start_date")  or data.get("start_date")  or "30daysAgo"
    end_date    = args.get("end_date")    or data.get("end_date")    or "today"

    limit_raw   = args.get("limit")       or data.get("limit")       or 10000
    offset_raw  = args.get("offset")      or data.get("offset")      or 0

    dims_raw    = args.get("dims")        or data.get("dims")

    if not property_id:
        return (json.dumps({"error": "Missing required parameter: property_id"}), 400, _cors_headers())

    try:
        limit = int(limit_raw)
    except Exception:
        limit = 10000

    try:
        offset = int(offset_raw)
    except Exception:
        offset = 0

    # Safe bounds
    if limit <= 0:
        limit = 10000
    if limit > 100000:
        limit = 100000
    if offset < 0:
        offset = 0

    # Default dimensions
    dims = [
        "sessionSourceMedium",
        "sessionSource",
        "sessionMedium",
        "sessionCampaignName",
    ]

    # Allow override via dims=dim1,dim2,...
    if dims_raw:
        dims = [d.strip() for d in str(dims_raw).split(",") if d.strip()]

    data_client = BetaAnalyticsDataClient(credentials=user_creds)
    prop = f"properties/{property_id}"
    dr = [DateRange(start_date=start_date, end_date=end_date)]

    req = RunReportRequest(
        property=prop,
        date_ranges=dr,
        dimensions=[Dimension(name=d) for d in dims],
        metrics=[
            Metric(name="sessions"),
            Metric(name="conversions"),
            Metric(name="sessionConversionRate"),
        ],
        order_bys=[OrderBy(metric={"metric_name": "sessions"}, desc=True)],
        limit=limit,
        offset=offset,
    )

    res = data_client.run_report(req)

    def safe_int(v):
        try:
            return int(float(v))
        except Exception:
            return 0

    def safe_float(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    rows = []
    if res.rows:
        for r in res.rows:
            out = {}
            # dimension values
            for i, d in enumerate(dims):
                out[d] = r.dimension_values[i].value if i < len(r.dimension_values) else ""
            # metric values
            out["sessions"] = safe_int(r.metric_values[0].value) if len(r.metric_values) > 0 else 0
            out["conversions"] = safe_int(r.metric_values[1].value) if len(r.metric_values) > 1 else 0
            out["sessionConversionRate"] = safe_float(r.metric_values[2].value) if len(r.metric_values) > 2 else 0.0
            rows.append(out)

    return (json.dumps({
        "propertyId": property_id,
        "dateRange": {"start_date": start_date, "end_date": end_date},
        "dims": dims,
        "limit": limit,
        "offset": offset,
        "rowCount": len(rows),
        "rows": rows,
    }), 200, _cors_headers())

# ============================
# FUNCTION: DEVICE + AGE + GENDER BREAKDOWNS
# ============================
def ga4_device_age_gender_breakdown_oauth(request):
    """
    Returns (for a GA4 property + date range):
      - Device breakdown: deviceCategory -> conversions, sessions, sessionConversionRate
      - Age breakdown: userAgeBracket -> conversions
      - Gender breakdown: userGender -> conversions

    Required:
      Header:
        Authorization: Bearer <ACCESS_TOKEN>  (analytics.readonly)

    Params (query string OR JSON body):
      property_id (required): e.g. "182279779"
      start_date (optional): default "30daysAgo" (or "2025-12-01")
      end_date   (optional): default "today" (or "2025-12-31")

    Response:
      {
        "propertyId": "...",
        "dateRange": {"start_date":"...", "end_date":"..."},
        "device": {
          "dimension": "deviceCategory",
          "rows": [{"device":"mobile","conversions":123,"sessions":456,"conversionRate":0.0123}, ...]
        },
        "age": {
          "dimension":"userAgeBracket",
          "rows":[{"age":"18-24","conversions":322}, ...]
        },
        "gender": {
          "dimension":"userGender",
          "rows":[{"gender":"male","conversions":1425}, ...]
        }
      }
    """
    pre = _handle_preflight(request)
    if pre:
        return pre

    try:
        user_creds, _ = _get_user_credentials_from_request(request)
    except ValueError as e:
        return (json.dumps({"error": str(e)}), 401, _cors_headers())

    # ---- parameters: query string or JSON body ----
    args = request.args or {}
    data = request.get_json(silent=True) or {}

    property_id = args.get("property_id") or data.get("property_id")
    start_date  = args.get("start_date")  or data.get("start_date")  or "30daysAgo"
    end_date    = args.get("end_date")    or data.get("end_date")    or "today"

    if not property_id:
        return (json.dumps({"error": "Missing required parameter: property_id"}), 400, _cors_headers())

    data_client = BetaAnalyticsDataClient(credentials=user_creds)
    prop = f"properties/{property_id}"
    dr = [DateRange(start_date=start_date, end_date=end_date)]

    def safe_int(v):
        try:
            return int(float(v))
        except Exception:
            return 0

    def safe_float(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    def run(req: RunReportRequest):
        return data_client.run_report(req)

    # -----------------------------
    # 1) DEVICE: deviceCategory
    # -----------------------------
    device_req = RunReportRequest(
        property=prop,
        date_ranges=dr,
        dimensions=[Dimension(name="deviceCategory")],
        metrics=[
            Metric(name="conversions"),
            Metric(name="sessions"),
            Metric(name="sessionConversionRate"),
        ],
        order_bys=[OrderBy(metric={"metric_name": "conversions"}, desc=True)],
        limit=50,
    )
    device_res = run(device_req)

    device_rows = []
    if device_res.rows:
        for r in device_res.rows:
            device = r.dimension_values[0].value if r.dimension_values else ""
            conv = safe_int(r.metric_values[0].value) if len(r.metric_values) > 0 else 0
            sess = safe_int(r.metric_values[1].value) if len(r.metric_values) > 1 else 0
            rate = safe_float(r.metric_values[2].value) if len(r.metric_values) > 2 else 0.0
            device_rows.append({
                "device": device,
                "conversions": conv,
                "sessions": sess,
                "conversionRate": rate,
            })

    # -----------------------------
    # 2) AGE: userAgeBracket
    # -----------------------------
    age_req = RunReportRequest(
        property=prop,
        date_ranges=dr,
        dimensions=[Dimension(name="userAgeBracket")],
        metrics=[Metric(name="conversions")],
        order_bys=[OrderBy(metric={"metric_name": "conversions"}, desc=True)],
        limit=50,
    )
    age_res = run(age_req)

    age_rows = []
    if age_res.rows:
        for r in age_res.rows:
            age = r.dimension_values[0].value if r.dimension_values else ""
            conv = safe_int(r.metric_values[0].value) if r.metric_values else 0
            age_rows.append({"age": age, "conversions": conv})

    # -----------------------------
    # 3) GENDER: userGender
    # -----------------------------
    gender_req = RunReportRequest(
        property=prop,
        date_ranges=dr,
        dimensions=[Dimension(name="userGender")],
        metrics=[Metric(name="conversions")],
        order_bys=[OrderBy(metric={"metric_name": "conversions"}, desc=True)],
        limit=50,
    )
    gender_res = run(gender_req)

    gender_rows = []
    if gender_res.rows:
        for r in gender_res.rows:
            g = r.dimension_values[0].value if r.dimension_values else ""
            conv = safe_int(r.metric_values[0].value) if r.metric_values else 0
            gender_rows.append({"gender": g, "conversions": conv})

    result = {
        "propertyId": property_id,
        "dateRange": {"start_date": start_date, "end_date": end_date},
        "device": {"dimension": "deviceCategory", "rows": device_rows},
        "age": {"dimension": "userAgeBracket", "rows": age_rows},
        "gender": {"dimension": "userGender", "rows": gender_rows},
        "notes": {
            "demographics": "Age/Gender may be empty due to consent, thresholding, or Google signals/demographics settings."
        }
    }

    return (json.dumps(result), 200, _cors_headers())


# ============================
# FUNCTION: LOCATION + INTERESTS (ALL + PAID CONVERSIONS)
# ============================
def ga4_location_interest_breakdown_oauth(request):
    """
    Returns:
      - totals: all conversions, paid conversions
      - location: Top N cities by ALL conversions + PAID conversions for same cities
      - interests: Top N brandingInterest by ALL conversions + PAID conversions for same interests
    """

    pre = _handle_preflight(request)
    if pre:
        return pre

    try:
        # -----------------------------
        # Auth
        # -----------------------------
        try:
            user_creds, _ = _get_user_credentials_from_request(request)
        except ValueError as e:
            return (json.dumps({"error": str(e)}), 401, _cors_headers())

        args = request.args or {}
        data = request.get_json(silent=True) or {}

        property_id = args.get("property_id") or data.get("property_id")
        start_date = args.get("start_date") or data.get("start_date") or "30daysAgo"
        end_date = args.get("end_date") or data.get("end_date") or "today"
        limit_raw = args.get("limit") or data.get("limit") or 5
        location_dim = args.get("location_dim") or data.get("location_dim") or "city"
        interests_dim = args.get("interests_dim") or data.get("interests_dim") or "brandingInterest"

        if not property_id:
            return (json.dumps({"error": "Missing required parameter: property_id"}), 400, _cors_headers())

        try:
            limit = int(limit_raw)
        except Exception:
            limit = 5
        limit = max(1, min(limit, 50))

        data_client = BetaAnalyticsDataClient(credentials=user_creds)
        prop = f"properties/{property_id}"
        dr = [DateRange(start_date=start_date, end_date=end_date)]

        def safe_int(v):
            try:
                return int(float(v))
            except Exception:
                return 0

        def run(req: RunReportRequest):
            return data_client.run_report(req)

        # -----------------------------
        # Paid filter (same intent as before)
        # -----------------------------
        paid_filter = FilterExpression(
            or_group=FilterExpressionList(
                expressions=[
                    FilterExpression(
                        filter=Filter(
                            field_name="sessionDefaultChannelGroup",
                            string_filter=Filter.StringFilter(
                                match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                                value="(?i)^paid",
                            ),
                        )
                    ),
                    FilterExpression(
                        filter=Filter(
                            field_name="sessionMedium",
                            string_filter=Filter.StringFilter(
                                match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                                value=r"(?i)^(cpc|cpm|ppc|paid)$",
                            ),
                        )
                    ),
                    FilterExpression(
                        filter=Filter(
                            field_name="sessionSourceMedium",
                            string_filter=Filter.StringFilter(
                                match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                                value=r"(?i)/(cpc|cpm|ppc|paid)$",
                            ),
                        )
                    ),
                ]
            )
        )

        # -----------------------------
        # 1) Totals (ALL conversions)
        # -----------------------------
        total_req = RunReportRequest(
            property=prop,
            date_ranges=dr,
            metrics=[Metric(name="conversions")],
        )
        total_res = run(total_req)
        total_conversions = safe_int(total_res.rows[0].metric_values[0].value) if total_res.rows else 0

        # -----------------------------
        # 2) Totals (PAID conversions)
        # -----------------------------
        paid_total_req = RunReportRequest(
            property=prop,
            date_ranges=dr,
            metrics=[Metric(name="conversions")],
            dimension_filter=paid_filter,
        )
        paid_total_res = run(paid_total_req)
        paid_conversions = safe_int(paid_total_res.rows[0].metric_values[0].value) if paid_total_res.rows else 0

        # -----------------------------
        # Helpers
        # -----------------------------
        def normalize_city_output(name: str) -> str:
            if not name:
                return ""
            n = name.strip()
            if n.lower() == "bucharest":
                return "Bucuresti"
            return n

        def is_valid_label(lbl: str) -> bool:
            if not lbl:
                return False
            l = lbl.strip().lower()
            return l not in ("(not set)", "not set", "(not provided)", "unknown")

        def breakdown_top_all_then_paid(dim_name: str, label_key: str, normalize_output_fn=None):
            """
            Robust approach without inListFilter:
              1) Top N labels by ALL conversions
              2) PAID conversions for the dimension (larger limit)
              3) Correlate locally for only the top labels
            """

            # --- ALL (top N)
            all_req = RunReportRequest(
                property=prop,
                date_ranges=dr,
                dimensions=[Dimension(name=dim_name)],
                metrics=[Metric(name="conversions")],
                order_bys=[OrderBy(metric={"metric_name": "conversions"}, desc=True)],
                limit=limit,
            )
            all_res = run(all_req)

            labels = []
            all_map = {}

            if all_res.rows:
                for r in all_res.rows:
                    raw_lbl = r.dimension_values[0].value if r.dimension_values else ""
                    conv = safe_int(r.metric_values[0].value) if r.metric_values else 0
                    if is_valid_label(raw_lbl):
                        labels.append(raw_lbl)
                        all_map[raw_lbl] = conv

            if not labels:
                return []

            # --- PAID (bigger limit), correlate locally
            # NOTE: We purposely DO NOT use inListFilter to avoid client/version incompatibilities.
            paid_req = RunReportRequest(
                property=prop,
                date_ranges=dr,
                dimensions=[Dimension(name=dim_name)],
                metrics=[Metric(name="conversions")],
                dimension_filter=paid_filter,
                order_bys=[OrderBy(metric={"metric_name": "conversions"}, desc=True)],
                limit=5000,  # enough headroom to include the top labels
            )
            paid_res = run(paid_req)

            paid_map = {}
            if paid_res.rows:
                for r in paid_res.rows:
                    raw_lbl = r.dimension_values[0].value if r.dimension_values else ""
                    conv = safe_int(r.metric_values[0].value) if r.metric_values else 0
                    if raw_lbl:
                        paid_map[raw_lbl] = paid_map.get(raw_lbl, 0) + conv

            out = []
            for raw_lbl in labels:
                out_lbl = normalize_output_fn(raw_lbl) if normalize_output_fn else raw_lbl
                out.append(
                    {
                        label_key: out_lbl,
                        "allConversions": all_map.get(raw_lbl, 0),
                        "paidConversions": paid_map.get(raw_lbl, 0),
                    }
                )
            return out

        # -----------------------------
        # 3) Location
        # -----------------------------
        location_rows = breakdown_top_all_then_paid(
            location_dim,
            "location",
            normalize_output_fn=normalize_city_output if location_dim == "city" else None,
        )

        # -----------------------------
        # 4) Interests
        # -----------------------------
        interests_rows = breakdown_top_all_then_paid(
            interests_dim,
            "interest",
        )

        result = {
            "propertyId": property_id,
            "dateRange": {"start_date": start_date, "end_date": end_date},
            "totals": {
                "all_conversions": total_conversions,
                "paid_conversions": paid_conversions,
            },
            "location": {
                "dimension": location_dim,
                "rows": location_rows,
            },
            "interests": {
                "dimension": interests_dim,
                "rows": interests_rows,
            },
            "notes": {
                "interests_dimension": "Uses GA4 dimension brandingInterest.",
                "paid_definition": "Paid inferred via sessionDefaultChannelGroup starts with 'Paid' OR medium/sourceMedium matching cpc/cpm/ppc/paid.",
                "correlation_method": "Top labels are selected by ALL conversions, then PAID conversions are mapped locally for the same labels (no inListFilter).",
            },
        }

        return (json.dumps(result), 200, _cors_headers())

    except Exception as e:
        # Return a JSON error (so you see the real reason in Apps Script logs)
        err = {
            "error": str(e),
            "trace": traceback.format_exc()[:8000],  # keep it bounded
        }
        return (json.dumps(err), 500, _cors_headers())


def ga4_property_top_landing_pages_oauth(request):
    """
    Returns top 5 landing pages for a GA4 property and date range, with:
      - total sessions
      - paid sessions
      - paid share
    Optional:
      - limit (default 5)
      - include_query_string (default true)
      - paid_dim (default landingPagePlusQueryString)

    Query/body params:
      - property_id   (required)
      - start_date    (optional, default 30daysAgo)
      - end_date      (optional, default today)
      - limit         (optional, default 5)
      - include_query_string (optional, true/false; default true)
    """
    pre = _handle_preflight(request)
    if pre:
        return pre

    try:
        try:
            user_creds, _ = _get_user_credentials_from_request(request)
        except ValueError as e:
            return (json.dumps({"error": str(e)}), 401, _cors_headers())

        # -----------------------------
        # Parameters: query string or JSON body
        # -----------------------------
        property_id = request.args.get("property_id") if request.args else None
        start_date = request.args.get("start_date") if request.args else None
        end_date = request.args.get("end_date") if request.args else None
        limit = request.args.get("limit") if request.args else None
        include_query_string = request.args.get("include_query_string") if request.args else None

        if not property_id:
            data = request.get_json(silent=True) or {}
            property_id = property_id or data.get("property_id")
            start_date = start_date or data.get("start_date")
            end_date = end_date or data.get("end_date")
            limit = limit or data.get("limit")
            include_query_string = include_query_string or data.get("include_query_string")

        if not property_id:
            return (json.dumps({"error": "Missing required parameter: property_id"}), 400, _cors_headers())

        start_date = start_date or "30daysAgo"
        end_date = end_date or "today"

        try:
            limit = int(limit) if limit is not None else 5
        except Exception:
            limit = 5

        limit = max(1, min(limit, 100))

        include_query_string = str(include_query_string).strip().lower() if include_query_string is not None else "true"
        include_query_string = include_query_string in ("true", "1", "yes", "y")

        # landingPagePlusQueryString is the GA4 landing page dimension for pages report style usage.
        # If you want cleaner grouping, use landingPage instead.
        landing_dim_name = "landingPagePlusQueryString" if include_query_string else "landingPage"

        data_client = BetaAnalyticsDataClient(credentials=user_creds)
        prop = f"properties/{property_id}"
        dr = [DateRange(start_date=start_date, end_date=end_date)]

        def run(req: RunReportRequest):
            return data_client.run_report(req)

        def safe_int(v):
            try:
                return int(float(v))
            except Exception:
                return 0

        def safe_float(v):
            try:
                return float(v)
            except Exception:
                return 0.0

        # -----------------------------
        # 1) Top landing pages by total sessions
        # -----------------------------
        # landingPage / landingPagePlusQueryString + sessions is valid for Core Reporting.
        # The Pages and screens predefined reports are built using the same Data API concepts. 
        # Docs confirm landing-page-style page dimensions and session metrics are available. :contentReference[oaicite:1]{index=1}
        top_req = RunReportRequest(
            property=prop,
            date_ranges=dr,
            dimensions=[Dimension(name=landing_dim_name)],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(metric={"metric_name": "sessions"}, desc=True)],
            limit=limit,
        )
        top_res = run(top_req)

        landing_pages = []
        page_keys = []

        if top_res.rows:
            for r in top_res.rows:
                lp = r.dimension_values[0].value or "(not set)"
                total_sessions = safe_int(r.metric_values[0].value)
                landing_pages.append({
                    "landingPage": lp,
                    "totalSessions": total_sessions,
                    "paidSessions": 0,
                    "paidShare": 0.0,
                })
                page_keys.append(lp)

        # If nothing returned, respond early
        if not landing_pages:
            result = {
                "propertyId": property_id,
                "dateRange": {"start_date": start_date, "end_date": end_date},
                "dimension": landing_dim_name,
                "rows": [],
            }
            return (json.dumps(result), 200, _cors_headers())

        # -----------------------------
        # 2) Paid sessions for those same landing pages
        # -----------------------------
        # Session-scoped dimensions are used in the paid filter:
        # - sessionDefaultChannelGroup
        # - sessionMedium
        # - sessionSourceMedium
        # These are valid GA4 Data API dimensions. :contentReference[oaicite:2]{index=2}
        paid_filter = FilterExpression(
            and_group=FilterExpressionList(expressions=[
                FilterExpression(
                    filter=Filter(
                        field_name=landing_dim_name,
                        in_list_filter=Filter.InListFilter(values=page_keys),
                    )
                ),
                FilterExpression(
                    or_group=FilterExpressionList(expressions=[
                        FilterExpression(
                            filter=Filter(
                                field_name="sessionDefaultChannelGroup",
                                string_filter=Filter.StringFilter(
                                    match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                                    value=r"(?i)^paid"
                                ),
                            )
                        ),
                        FilterExpression(
                            filter=Filter(
                                field_name="sessionMedium",
                                string_filter=Filter.StringFilter(
                                    match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                                    value=r"(?i)^(cpc|cpm|ppc|paid|display|paid social)$"
                                ),
                            )
                        ),
                        FilterExpression(
                            filter=Filter(
                                field_name="sessionSourceMedium",
                                string_filter=Filter.StringFilter(
                                    match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                                    value=r"(?i)/(cpc|cpm|ppc|paid|display)$"
                                ),
                            )
                        ),
                    ])
                )
            ])
        )

        paid_req = RunReportRequest(
            property=prop,
            date_ranges=dr,
            dimensions=[Dimension(name=landing_dim_name)],
            metrics=[Metric(name="sessions")],
            dimension_filter=paid_filter,
            order_bys=[OrderBy(metric={"metric_name": "sessions"}, desc=True)],
            limit=max(limit, len(page_keys)),
        )
        paid_res = run(paid_req)

        paid_map = {}
        if paid_res.rows:
            for r in paid_res.rows:
                lp = r.dimension_values[0].value or "(not set)"
                paid_sessions = safe_int(r.metric_values[0].value)
                paid_map[lp] = paid_sessions

        # -----------------------------
        # 3) Merge totals + paid
        # -----------------------------
        for row in landing_pages:
            paid_sessions = paid_map.get(row["landingPage"], 0)
            total_sessions = row["totalSessions"]
            row["paidSessions"] = paid_sessions
            row["paidShare"] = (paid_sessions / total_sessions) if total_sessions > 0 else 0.0

        result = {
            "propertyId": property_id,
            "dateRange": {"start_date": start_date, "end_date": end_date},
            "dimension": landing_dim_name,
            "rows": landing_pages,
        }

        return (json.dumps(result), 200, _cors_headers())

    except Exception as e:
        err = {
            "error": str(e),
            "traceback": traceback.format_exc(),
        }
        return (json.dumps(err), 500, _cors_headers())
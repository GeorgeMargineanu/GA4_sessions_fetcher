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
            filter=Filter(
                field_name="sessionDefaultChannelGroup",
                string_filter=Filter.StringFilter(
                    match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                    value="(?i)^paid|cpc|cpm|ppc"  # case-insensitive
                ),
            )
        )

        paid_req = RunReportRequest(
            property=prop,
            date_ranges=dr,
            dimensions=[Dimension(name="sessionDefaultChannelGroup")],  # <-- important
            metrics=[Metric(name="conversions")],
            dimension_filter=paid_filter,
            limit=100,
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
        # 🔥 This is what you were missing: real error output
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

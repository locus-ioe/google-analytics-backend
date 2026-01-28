from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import secrets
from fastapi import FastAPI, HTTPException, Depends, Query


load_dotenv()

app = FastAPI()
security = HTTPBasic()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID")

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    is_username_correct = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    is_password_correct = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    
    if not (is_username_correct and is_password_correct):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username

def get_ga4_data(client, start_date, end_date, metrics, dimensions=None):
    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        metrics=[Metric(name=m) for m in metrics],
        dimensions=[Dimension(name=d) for d in dimensions] if dimensions else []
    )
    response = client.run_report(request)
    
    results = []
    for row in response.rows:
        item = {}
        if dimensions:
            for i, dim in enumerate(row.dimension_values):
                item[response.dimension_headers[i].name] = dim.value
        for i, metric in enumerate(row.metric_values):
            item[response.metric_headers[i].name] = metric.value
        results.append(item)
    
    return results

def format_duration(seconds):
    """Convert seconds to MM:SS format"""
    try:
        mins = int(float(seconds)) // 60
        secs = int(float(seconds)) % 60
        return f"{mins}:{secs:02d}"
    except:
        return "0:00"

def format_date_label(date_str):
    """Convert YYYYMMDD to 'Mon DD' format"""
    try:
        date_obj = datetime.strptime(date_str, "%Y%m%d")
        return date_obj.strftime("%b %d")
    except:
        return date_str

@app.get("/analytics")
def get_analytics(
    days: int = Query(
        30,
        ge=1,           # minimum 1 day
        le=365,         # reasonable upper limit (you can increase)
        description="Number of days to look back (7, 30, 90 commonly used)"
    ),
    admin: str = Depends(verify_admin)
):
    try:
        client = BetaAnalyticsDataClient()

        # ── Calculate date range ────────────────────────────────────────
        today = datetime.now().date()
        start_date_obj = today - timedelta(days=days - 1)   # inclusive
        start_date = start_date_obj.strftime("%Y-%m-%d")
        end_date   = today.strftime("%Y-%m-%d")

        # ── Overview (all metrics for the selected period) ──────────────
        overview_raw = get_ga4_data(
            client,
            start_date,
            end_date,
            [
                "totalUsers", "newUsers", "sessions", "engagedSessions",
                "screenPageViews", "averageSessionDuration", "bounceRate",
                "engagementRate", "conversions"
            ]
        )

        overview = {}
        if overview_raw and len(overview_raw) > 0:
            data = overview_raw[0]
            total_sessions = float(data.get("sessions", 0))
            overview = {
                "periodDays": days,
                "startDate": start_date,
                "endDate": end_date,
                "totalUsers": int(float(data.get("totalUsers", 0))),
                "newUsers": int(float(data.get("newUsers", 0))),
                "sessions": int(total_sessions),
                "engagedSessions": int(float(data.get("engagedSessions", 0))),
                "pageViews": int(float(data.get("screenPageViews", 0))),
                "avgSessionDuration": format_duration(data.get("averageSessionDuration", "0")),
                "bounceRate": round(float(data.get("bounceRate", 0)) * 100, 1),
                "engagementRate": round(float(data.get("engagementRate", 0)) * 100, 1),
                "conversions": int(float(data.get("conversions", 0))),
                "conversionRate": round(
                    float(data.get("conversions", 0)) / max(total_sessions, 1) * 100, 1
                ),
            }

        # ── Traffic over time (daily breakdown) ─────────────────────────
        traffic_raw = get_ga4_data(
            client,
            start_date,
            end_date,
            ["activeUsers", "sessions", "screenPageViews", "engagedSessions"],
            ["date"]
        )

        traffic_over_time = {
            "labels": [],
            "activeUsers": [],
            "sessions": [],
            "pageViews": [],
            "engagedSessions": [],
        }

        for row in sorted(traffic_raw, key=lambda x: x.get("date", "")):  # ensure chronological
            traffic_over_time["labels"].append(format_date_label(row.get("date", "")))
            traffic_over_time["activeUsers"].append(int(float(row.get("activeUsers", 0))))
            traffic_over_time["sessions"].append(int(float(row.get("sessions", 0))))
            traffic_over_time["pageViews"].append(int(float(row.get("screenPageViews", 0))))
            traffic_over_time["engagedSessions"].append(int(float(row.get("engagedSessions", 0))))

        # ── Top pages ───────────────────────────────────────────────────
        top_pages_raw = get_ga4_data(
            client,
            start_date,
            end_date,
            ["screenPageViews", "averageSessionDuration", "bounceRate", "engagedSessions"],
            ["pagePath"]
        )[:10]   # you can adjust the limit

        top_pages = [
            {
                "page": item.get("pagePath", "(not set)"),
                "views": int(float(item.get("screenPageViews", 0))),
                "avgTime": format_duration(item.get("averageSessionDuration", "0")),
                "bounceRate": round(float(item.get("bounceRate", 0)) * 100, 1),
                "engagedSessions": int(float(item.get("engagedSessions", 0))),
            }
            for item in top_pages_raw
        ]

        # ── Device breakdown ────────────────────────────────────────────
        devices_raw = get_ga4_data(
            client,
            start_date,
            end_date,
            ["activeUsers"],
            ["deviceCategory"]
        )

        device_map = {"desktop": 0, "mobile": 0, "tablet": 0}
        total_device_users = 0

        for item in devices_raw:
            cat = item.get("deviceCategory", "other").lower()
            users = int(float(item.get("activeUsers", 0)))
            if cat in device_map:
                device_map[cat] += users
            total_device_users += users

        device_category = {
            "labels": ["Desktop", "Mobile", "Tablet"],
            "data": [
                round(device_map["desktop"] / max(total_device_users, 1) * 100, 1),
                round(device_map["mobile"]   / max(total_device_users, 1) * 100, 1),
                round(device_map["tablet"]   / max(total_device_users, 1) * 100, 1),
            ],
            "colors": ["#3b82f6", "#10b981", "#8b5cf6"]   # you can keep/change
        }

        # ── Traffic sources (channel groups) ────────────────────────────
        sources_raw = get_ga4_data(
            client,
            start_date,
            end_date,
            ["sessions", "activeUsers"],
            ["sessionDefaultChannelGroup"]
        )[:8]

        traffic_sources = {
            "labels": [],
            "sessions": [],
            "users": [],
        }

        for item in sources_raw:
            traffic_sources["labels"].append(item.get("sessionDefaultChannelGroup", "(other)"))
            traffic_sources["sessions"].append(int(float(item.get("sessions", 0))))
            traffic_sources["users"].append(int(float(item.get("activeUsers", 0))))

        # Final response
        return {
            "status": "success",
            "days": days,
            "startDate": start_date,
            "endDate": end_date,
            "data": {
                "overview": overview,
                "trafficOverTime": traffic_over_time,
                "topPages": top_pages,
                "deviceCategory": device_category,
                "trafficSources": traffic_sources,
                # "userEngagement": ... → consider removing or improving later
            },
            "timestamp": datetime.now().isoformat()
        }

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=f"Invalid days value: {str(ve)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analytics error: {str(e)}")
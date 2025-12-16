import json
from google.analytics.admin import AnalyticsAdminServiceClient

def main():
    client = AnalyticsAdminServiceClient()
    accounts_data = []

    for summary in client.list_account_summaries():
        account_entry = {
            "account": summary.account,
            "displayName": summary.display_name,
            "properties": []
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

    print(json.dumps({"accounts": accounts_data}, indent=2))

if __name__ == "__main__":
    main()

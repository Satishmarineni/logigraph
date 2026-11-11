"""X12 code lists used by the 214 parser (descriptions from the X12 standard).

AT701 = Shipment Status Indicator Code (element 1650)
AT702 = Shipment Status or Appointment Reason Code (element 1651)
"""

STATUS_CODE_NAMES = {
    "AF": "Carrier Departed Pickup Location with Shipment",
    "P1": "Departed Terminal Location",
    "AG": "Estimated Delivery",
    "SD": "Shipment Delayed",
    "X4": "Arrived at Terminal Location",
    "D1": "Completed Unloading at Delivery Location",
    "X1": "Arrived at Delivery Location",
    "X3": "Arrived at Pickup Location",
    "CP": "Completed Loading at Pickup Location",
    "AV": "Available for Delivery",
    "K1": "Arrived at Customs",
}

REASON_CODE_NAMES = {
    "NS": "Normal Status",
    "AO": "Weather or Natural Disaster Related",
    "CA": "Customs (Import or Export)",
    "BC": "Missing Documents",
    "AS": "Hold Due to Customs Documentation Problems",
    "AG": "Consignee Related",
    "B1": "Consignee Closed",
    "AJ": "Other Carrier Related",
    "AI": "Mechanical Breakdown",
    "AM": "Shipper Related",
}

# Business meaning of the status codes we rely on
ESTIMATED_DELIVERY = "AG"
DELIVERED = {"D1", "X1"}
DELAYED = "SD"
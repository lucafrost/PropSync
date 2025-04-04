from typing import Any, List, OrderedDict, Optional
from urllib.parse import quote
import xmltodict
import pydantic
import requests
import boto3
import json
import re
import os


# ----- ENVIRONMENT VARIABLES -----

WEBFLOW_SECRET = os.environ["WEBFLOW_SECRET"]
WF_COLLECTION = os.environ["WF_COLLECTION"]
XML_ENDPOINT = os.environ["XML_ENDPOINT"]
AWS_REGION = os.environ["AWS_REGION"]


# ----- PYDANTIC MODEL -----

class ListingType(pydantic.BaseModel):
    # Metadata
    KendalRef: str
    PropertyName: str
    PropType: str
    ShortDesc: str
    LongDesc: str
    Address: str
    AskingPrice: str
    SizeSqft: str
    NumBeds: str
    NumBaths: str
    # Property Images
    ImageOne: str
    ImageTwo: str
    ImageThree: str
    ImageFour: str
    ImageFive: str
    # Agent Details
    AgentName: Optional[str] = None
    AgentAvatar: Optional[str] = None
    AgentEmail: Optional[str] = None
    AgentTel: Optional[str] = None


# ----- Main Codebase -----

class KendalAgent:
    def __init__(self, xml_endpoint: str,
                 webflow_secret: str,
                 webflow_collection: str,
                 boto_session: boto3.Session = None) -> None:
        """
        Initialise a new KendalAgent object.

        Parameters
        ----------
        xml_endpoint : str
            URL of the endpoint exposed by Kendal.
        webflow_secret : str
            ARN of Secret in AWS Secrets Manager containing Webflow site token.
        webflow_collection: str
            Webflow Collection ID from CMS.
        boto_session : boto3.Session
            Pre-initialised Boto3 Session for AWS Authentication.
        """
        if not boto_session:
            boto_session = boto3.Session(region_name=AWS_REGION)
        self.boto_session = boto_session
        self.shh = self.boto_session.client("secretsmanager")
        self.webflow_key = self.shh.get_secret_value(SecretId=webflow_secret)["SecretString"]
        self.xml_endpoint = xml_endpoint
        self.webflow_collection = webflow_collection

    def run(self):
        # [1.] PRE-PROCESSING
        feed = self._read_feed(self.xml_endpoint)
        properties = self._extract_properties(feed)
        properties = self._serialise_all(properties)
        # [2.] DELETE ALL WEBFLOW CMS ITEMS
        live_ids = self._get_all_item_ids(live=True, collection_id=self.webflow_collection)
        draft_ids = self._get_all_item_ids(live=False, collection_id=self.webflow_collection)
        self._delete_items(live=True, collection_id=self.webflow_collection, item_ids=live_ids)
        self._delete_items(live=False, collection_id=self.webflow_collection, item_ids=draft_ids)
        # [3.] CREATE ALL NEW ITEMS IN BULK
        self._create_bulk_items(self.webflow_collection, properties)

    def _read_feed(self, url: str) -> OrderedDict[str, Any]:
        r = requests.get(url)
        if not r.ok:
            raise RuntimeError(f"Could not get XML feed, status code {r.status_code} returned.")
        return xmltodict.parse(r.text)

    def _extract_properties(self, xml_feed: OrderedDict) -> List[dict]:
        if type(xml_feed["list"]["property"]) is dict:
            return [xml_feed["list"]["property"]]
        else:
            return xml_feed["list"]["property"]

    def _serialise_all(self, properties: List[dict]) -> List[ListingType]:
        serialised: list[ListingType] = []
        for prop in properties:
            short_desc, long_desc = self._split_desc(prop["description_en"])
            list_obj = ListingType(
                KendalRef=prop["reference_number"],
                PropertyName=prop["title_en"],
                PropType=prop["property_type"],
                ShortDesc=short_desc,
                LongDesc=long_desc,
                Address=f'{prop["property_name"]}, {prop["community"]}, {prop["city"]}',
                SizeSqft="{:,}".format(int(prop["size"])),
                AskingPrice="AED {:,}".format(int(prop["askingPrice"]["value"])),
                NumBeds=f'{prop["bedroom"]} Bedrooms',
                NumBaths=f'{prop["bathroom"]} Bathrooms',
                ImageOne=quote(prop["photo"]["url"][0], safe=':/'),  # Encode URL, keep :/ safe
                ImageTwo=quote(prop["photo"]["url"][1], safe=':/'),
                ImageThree=quote(prop["photo"]["url"][2], safe=':/'),
                ImageFour=quote(prop["photo"]["url"][3], safe=':/'),
                ImageFive=quote(prop["photo"]["url"][4], safe=':/'),
                AgentName=prop["agent"]["name"],
                AgentAvatar=quote(prop["agent"]["photo"], safe=':/'),
                AgentEmail=prop["agent"]["email"],
                AgentTel=prop["agent"]["phone"]
            )
            serialised.append(list_obj)
        return serialised

    def _split_desc(self, property_desc: str) -> [str, str]:
        parts = re.split(r'\n+', property_desc)
        parts = [part.strip() for part in parts if part.strip()]
        short_desc = ""
        long_desc = ""
        if parts:
            short_desc = parts[0]
            if len(parts) > 1:
                long_desc = '\n'.join(parts[1:])
        return short_desc, long_desc

    def _get_all_item_ids(self, live: bool, collection_id: str) -> List[dict]:
        if live:
            url = f"https://api.webflow.com/v2/collections/{collection_id}/items/live?limit=100"
        else:
            url = f"https://api.webflow.com/v2/collections/{collection_id}/items?limit=100"
        headers = {"Authorization": f"Bearer {self.webflow_key}"}
        r = requests.request("GET", url, headers=headers)
        if not r.ok:
            raise RuntimeError(f"Failed to list all Webflow items, status code {r.status_code} returned")
        return [{"id": item["id"]} for item in r.json()["items"]]

    def _delete_items(self, live: bool, collection_id: str, item_ids: List[dict]):
        if live:
            url = f"https://api.webflow.com/v2/collections/{collection_id}/items/live"
        else:
            url = f"https://api.webflow.com/v2/collections/{collection_id}/items"
        headers = {
            "Authorization": f"Bearer {self.webflow_key}",
            "Content-Type": "application/json"
        }
        payload = {"items": item_ids}
        json_payload = json.dumps(payload)
        r = requests.delete(url, headers=headers, data=json_payload)
        if not r.ok:
            error_message = f"Failed to delete all Webflow items, status code {r.status_code} returned: {r.text}"
            raise RuntimeError(error_message)

    def _create_bulk_items(self, collection_id: str, properties: List[ListingType]):
        url = f"https://api.webflow.com/v2/collections/{collection_id}/items/live"
        headers = {
            "Authorization": f"Bearer {self.webflow_key}",
            "Content-Type": "application/json"
        }
        items = [{
            "isArchived": False,
            "isDraft": False,
            "fieldData": {
                "name": prop.PropertyName,
                "slug": prop.KendalRef,
                "property-description": prop.ShortDesc,
                "property-sqaure-fit": prop.SizeSqft,
                "property-bedroom": prop.NumBeds,
                "property-bathroom": prop.NumBaths,
                "property-overview": prop.LongDesc,
                "property-price": prop.AskingPrice,
                "property-type": prop.PropType,
                "property-address": prop.Address,
                "property-image": {
                    "fileId": None,
                    "url": prop.ImageOne
                },
                "property-smal-image-1": {
                    "fileId": None,
                    "url": prop.ImageTwo
                },
                "property-smal-image-2": {
                    "fileId": None,
                    "url": prop.ImageThree
                },
                "property-smal-image-3": {
                    "fileId": None,
                    "url": prop.ImageFour
                },
                "property-smal-image-4": {
                    "fileId": None,
                    "url": prop.ImageFive
                },
                "agentname": prop.AgentName,
                "agentemail": prop.AgentEmail,
                "agenttel": prop.AgentTel,
                "agentavatar": {
                    "fileId": None,
                    "url": prop.AgentAvatar
                }
            }
        } for prop in properties]
        payload = {"items": items}
        json_payload = json.dumps(payload)
        r = requests.post(url, headers=headers, data=json_payload)
        if not r.ok:
            error_message = f"Failed to create bulk Webflow items, status code {r.status_code} returned: {r.text}"
            raise RuntimeError(error_message)
        return r


# ----- LAMBDA RUNTIME -----

ka = KendalAgent(xml_endpoint=XML_ENDPOINT,
                 webflow_secret=WEBFLOW_SECRET,
                 webflow_collection=WF_COLLECTION)

def lambda_handler(event: dict, context: dict):
    ka.run()
    print("Successfully synced Kendal with Webflow")

lambda_handler({}, {})

# if __name__ == "__main__":
#     boto_sess = boto3.Session(region_name=AWS_REGION, profile_name="ccre")
#     ka = KendalAgent(xml_endpoint=XML_ENDPOINT,
#                      webflow_secret=WEBFLOW_SECRET,
#                      webflow_collection=WF_COLLECTION,
#                      boto_session=boto_sess)
#     ka.run()
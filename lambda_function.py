from typing import Any, List, OrderedDict
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
    ImageOne: str
    ImageTwo: str
    ImageThree: str
    ImageFour: str
    ImageFive: str


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
        cms_ids = self._get_all_item_ids(self.webflow_collection)
        self._delete_items(self.webflow_collection, cms_ids)
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
                ImageOne=prop["photo"]["url"][0],
                ImageTwo=prop["photo"]["url"][1],
                ImageThree=prop["photo"]["url"][2],
                ImageFour=prop["photo"]["url"][3],
                ImageFive=prop["photo"]["url"][4]
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

    def _get_all_item_ids(self, collection_id: str) -> List[dict]:
        url = f"https://api.webflow.com/v2/collections/{collection_id}/items/live?limit=100"
        headers = {"Authorization": f"Bearer {self.webflow_key}"}
        r = requests.request("GET", url, headers=headers)
        if not r.ok:
            raise RuntimeError(f"Failed to list all Webflow items, status code {r.status_code} returned")
        return [{"id": item["id"]} for item in r.json()["items"]]

    def _delete_items(self, collection_id: str, item_ids: List[dict]):
        url = f"https://api.webflow.com/v2/collections/{collection_id}/items/live"
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
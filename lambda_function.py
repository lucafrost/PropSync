from typing import (
    Any,
    List,
    OrderedDict,
    Optional,
    Union,
    Literal
)
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
POA_VALUE = os.environ["POA_VALUE"]
CS_VALUE = os.environ["CS_VALUE"]

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
                 poa_value: Union[Literal[False], int],
                 cs_value: Union[Literal[False], int],
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
        poa_value : Union[False, int]
            Optional. Value at which the price changes to
            "Price on Application". See notes. Set to False to
            disable this behaviour.
        cs_value : Union[False, int]
            Optional. Value at which the price changes to
            "Coming Soon". See notes. Set to False to
            disable this behaviour.
        boto_session : boto3.Session
            Pre-initialised Boto3 Session for AWS Authentication.

        Notes
        -----
        Kendal does not support setting properties to POA, instead,
        they require an integer for the price field. By setting the
        `poa_value` to an integer (e.g. 999), any properties priced
        at that value will have their price set in Webflow as
        "price on application".

        As with `poa_value`, the `cs_value` field can be used to set
        the price field to "Coming Soon".
        """
        if not boto_session:
            boto_session = boto3.Session(region_name=AWS_REGION)
        self.boto_session = boto_session
        self.shh = self.boto_session.client("secretsmanager")
        self.webflow_key = self.shh.get_secret_value(SecretId=webflow_secret)["SecretString"]
        self.xml_endpoint = xml_endpoint
        self.webflow_collection = webflow_collection
        self.poa_value = poa_value
        self.cs_value = cs_value

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
                SizeSqft=self._prop_size_handler(prop),
                AskingPrice=self._price_handler(int(prop["askingPrice"]["value"])),
                NumBeds=self._bed_bath_handler(prop, "beds"),
                NumBaths=self._bed_bath_handler(prop, "baths"),
                ImageOne=self._serialise_url(prop["photo"]["url"][0]),
                ImageTwo=self._serialise_url(prop["photo"]["url"][1]),
                ImageThree=self._serialise_url(prop["photo"]["url"][2]),
                ImageFour=self._serialise_url(prop["photo"]["url"][3]),
                ImageFive=self._serialise_url(prop["photo"]["url"][4]),
                AgentName=prop["agent"]["name"],
                AgentAvatar=self._serialise_url(prop["agent"]["photo"]),
                AgentEmail=prop["agent"]["email"],
                AgentTel=prop["agent"]["phone"]
            )
            serialised.append(list_obj)
        return serialised

    def _serialise_url(self, url: str) -> str:
        """
        Method to wrap urllib.parse.quote to format/serialise URLs.
        This is necessary as a great deal of images on Kendal have
        filenames from WhatsApp and contain spaces (e.g. WhatsApp
        Image 2025-01-01.jpg)

        Parameters
        ----------
        url : str
            Input URL
        """
        return quote(url, safe=':/')

    def _price_handler(self, price: int) -> str:
        if price == self.poa_value:
            return "Price on Application"
        elif price == self.cs_value:
            return "Coming Soon"
        else:
            return "AED {:,}".format(price)

    def _prop_size_handler(self, prop: dict) -> str:
        if type(prop["size"]) == str:
            return "{:,}".format(int(prop["size"]))
        else:
            return "{:,}".format(int(prop["size"]["value"]))

    def _bed_bath_handler(self, prop: dict, target: Literal["beds", "baths"]) -> str:
        if target == "beds": # handling num bedrooms
            if type(prop["bedroom"]) == dict:
                return f'{prop["bedroom"]["value"]} Bedrooms'
            elif type(prop["bedroom"]) == str:
                return f'{prop["bedroom"]} Bedrooms'
        elif target == "baths": # handling num bathrooms
            if type(prop["bathroom"]) == dict:
                return f'{prop["bathroom"]["value"]} Bathrooms'
            elif type(prop["bathroom"]) == str:
                return f'{prop["bathroom"]} Bathrooms'
        else:
            raise RuntimeError(f"Target `{target}` is not supported!")

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
                 poa_value=POA_VALUE,
                 cs_value=CS_VALUE,
                 webflow_collection=WF_COLLECTION)

def lambda_handler(event: dict, context: dict):
    ka.run()
    print("Successfully synced Kendal with Webflow")


# if __name__ == "__main__":
#     boto_sess = boto3.Session(region_name=AWS_REGION, profile_name="ccre")
#     ka = KendalAgent(xml_endpoint=XML_ENDPOINT,
#                      webflow_secret=WEBFLOW_SECRET,
#                      webflow_collection=WF_COLLECTION,
#                      poa_value=POA_VALUE,
#                      cs_value=CS_VALUE,
#                      boto_session=boto_sess)
#     ka.run()
from typing import (
    Any,
    List,
    OrderedDict,
    Optional,
    Union,
    Literal
)
from urllib.parse import quote, urlparse, parse_qs
import xmltodict
import pydantic
import requests
import boto3
import json
import re
import os


# ----- ENVIRONMENT VARIABLES -----

VIDEO_LISTINGS_FILE = os.environ["VIDEO_LISTINGS_FILE"]
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
    # ImageSix: str
    # ImageSeven: str
    # ImageEight: str
    # ImageNine: str
    # ImageTen: str
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
                 video_listings_file: Union[Literal[False], str],
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
            ARN of Secret in AWS Secrets Manager containing Webflow
            site token.
        webflow_collection: str
            Webflow Collection ID from CMS.
        video_listings_file : Union[False, str]
            Optional. Path to JSON file containing a dictionary of
            property refs from Kendal and YouTube video URLs.
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
        if video_listings_file:
            with open(video_listings_file) as f:
                self.videos: dict = dict(json.load(f))
        else:
            self.videos = False

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
            short_desc, long_desc = self._fmt_desc(prop["description_en"])
            list_obj = ListingType(
                KendalRef=prop["reference_number"],
                PropertyName=prop["title_en"],
                PropType=prop["property_type"],
                ShortDesc=short_desc,
                LongDesc=long_desc,
                Address=f'{prop["property_name"]}, {prop["community"]}, {prop["city"]}',
                SizeSqft=self._prop_size_handler(prop),
                AskingPrice=self._price_handler(prop["askingPrice"]),
                NumBeds=self._bed_bath_handler(prop, "bedroom"),
                NumBaths=self._bed_bath_handler(prop, "bathroom"),
                ImageOne=self._serialise_url(prop["photo"]["url"][0]),
                ImageTwo=self._serialise_url(prop["photo"]["url"][1]),
                ImageThree=self._serialise_url(prop["photo"]["url"][2]),
                ImageFour=self._serialise_url(prop["photo"]["url"][3]),
                ImageFive=self._serialise_url(prop["photo"]["url"][4]),
                # ImageSix=self._serialise_url(prop["photo"]["url"][5]),
                # ImageSeven=self._serialise_url(prop["photo"]["url"][6]),
                # ImageEight=self._serialise_url(prop["photo"]["url"][7]),
                # ImageNine=self._serialise_url(prop["photo"]["url"][8]),
                # ImageTen=self._serialise_url(prop["photo"]["url"][9]),
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

    def _price_handler(self, price_data: Union[int, dict]) -> str:
        """
        Handle price formatting for both fixed and range values.

        Parameters
        ----------
        price_data : Union[int, dict]
            The askingPrice data from the XML feed. Can be an int (for older format)
            or a dict with 'type', 'value', 'min', and 'max' keys.

        Returns
        -------
        str
            Formatted price string (e.g., "AED 1,234,567", "AED 999k - 2.3m", "Price on Application")
        """
        def format_price(value: int) -> str:
            """Helper function to format a single price value into k or m notation."""
            if value >= 1_000_000:  # Millions
                return f"{value / 1_000_000:.1f}m".replace(".0", "")
            elif value >= 10_000:  # Thousands (only for larger numbers to avoid 9k, etc.)
                return f"{value / 1_000:.0f}k"
            else:
                return "{:,}".format(value)
        # handle legacy integer input
        if isinstance(price_data, int):
            if price_data == self.poa_value:
                return "Price on Application"
            elif price_data == self.cs_value:
                return "Coming Soon"
            else:
                return f"AED {format_price(price_data)}"
        # handle new dict input from XML feed
        if not isinstance(price_data, dict):
            raise ValueError("price_data must be an int or dict")
        price_type = price_data.get("type")
        if price_type == "fixed":
            value = int(price_data["value"])
            if value == self.poa_value:
                return "Price on Application"
            elif value == self.cs_value:
                return "Coming Soon"
            else:
                return f"AED {format_price(value)}"
        elif price_type == "range":
            min_value, max_value = int(price_data["min"]), int(price_data["max"])
            return f"AED {format_price(min_value)} - {format_price(max_value)}"
        else:
            raise ValueError(f"Unsupported price type: {price_type}")

    def _prop_size_handler(self, prop: dict) -> str:
        if type(prop["size"]) == str:
            return "BUA {:,} sqft".format(int(prop["size"]))
        else:
            if prop["size"]["type"] == "fixed":
                return "BUA {:,} sqft".format(int(prop["size"]["value"]))
            elif prop["size"]["type"] == "range":
                return "BUA {:,}-{:,} sqft".format(int(prop["size"]["min"]), int(prop["size"]["max"]))
            else:
                raise RuntimeError("Property size type is unsupported")

    def _bed_bath_handler(self, prop: dict, target: Literal["bedroom", "bathroom"]) -> str:
        verbose_target = target.capitalize() + "s"
        if type(prop[target]) == dict:
            if prop[target]["type"] == "fixed":
                return f'{prop[target]["value"]} {verbose_target}'
            elif prop[target]["type"] == "range":
                return f'{prop[target]["min"]}-{prop[target]["max"]} {verbose_target}'
        elif type(prop[target]) == str:
            return f'{prop[target]} {verbose_target}'

    def _fmt_desc(self, property_desc: str) -> [str, str]:
        parts = re.split(r'\n+', property_desc)
        parts = [part.strip() for part in parts if part.strip()]
        short_desc = ""
        long_desc = ""
        if parts:
            short_desc = parts[0]
            if len(parts) > 1:
                long_desc = '\n'.join(parts[1:])
        long_desc = long_desc.replace("\n", "<br><br>")
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

    def _extract_youtube_id(self, url: str) -> str:
        query = urlparse(url)
        if query.hostname == 'youtu.be':
            return query.path[1:]
        if query.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
            if query.path == '/watch':
                p = parse_qs(query.query)
                return p['v'][0]
            if query.path[:7] == '/embed/':
                return query.path.split('/')[2]
            if query.path[:3] == '/v/':
                return query.path.split('/')[2]

    def _video_handler(self, property_ref: str) -> Union[Literal[False], str]:
        if property_ref in self.videos.keys():
            youtube_url = self.videos[property_ref]
            return self._extract_youtube_id(youtube_url)
        else:
            return ""

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
                # "image-six": {
                #     "fileId": None,
                #     "url": prop.ImageSix
                # },
                # "image-seven": {
                #     "fileId": None,
                #     "url": prop.ImageSeven
                # },
                # "image-eight": {
                #     "fileId": None,
                #     "url": prop.ImageEight
                # },
                # "image-nine": {
                #     "fileId": None,
                #     "url": prop.ImageNine
                # },
                # "image-ten": {
                #     "fileId": None,
                #     "url": prop.ImageTen
                # },
                "agentname": prop.AgentName,
                "agentemail": prop.AgentEmail,
                "agenttel": prop.AgentTel,
                "agentavatar": {
                    "fileId": None,
                    "url": prop.AgentAvatar
                },
                "video-one": self._video_handler(prop.KendalRef),
                "video-2": True if prop.KendalRef in self.videos.keys() else False
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
                 poa_value=int(POA_VALUE),
                 cs_value=int(CS_VALUE),
                video_listings_file=VIDEO_LISTINGS_FILE,
                 webflow_collection=WF_COLLECTION)

def lambda_handler(event: dict, context: dict):
    ka.run()
    print("Successfully synced Kendal with Webflow")


# if __name__ == "__main__":
#     boto_sess = boto3.Session(region_name=AWS_REGION, profile_name="ccre")
#     ka = KendalAgent(xml_endpoint=XML_ENDPOINT,
#                      webflow_secret=WEBFLOW_SECRET,
#                      webflow_collection=WF_COLLECTION,
#                      poa_value=int(POA_VALUE),
#                      cs_value=int(CS_VALUE),
#                      video_listings_file=VIDEO_LISTINGS_FILE,
#                      boto_session=boto_sess)
#     ka.run()
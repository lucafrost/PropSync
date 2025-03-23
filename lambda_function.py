"""
Notes
-----
This Lambda Function queries an XML Endpoint exposed by the
Kendal CRM [1] containing a list of published properties.

The XML is then converted into JSON/dictionary format using
`xmltodict` [2], before being serialised using a custom
Pydantic type.

TODO:
    - [ ] Change KENDAL_ENDPOINT to environment variable
    - [ ] Create Lambda Layer (w/ tf trigger) for xmltodict




References
----------
[1] Kendal XML Documentation
    https://kendal-ai.notion.site/XML-Feed-Documentation-for-Listings-13fa8cf7e41780d786aef6eec2357bc7
[2] PyPI xmltodict
    https://pypi.org/project/xmltodict/
"""

from typing import Union, Literal
import xmltodict
import requests
import pydantic
import os

KENDAL_ENDPOINT = "https://firebasestorage.googleapis.com/v0/b/kendal-testing.appspot.com/o/xml-feed%2FNxN2EaxsPdWlcVwIoadI%2Fexternal_website.xml?alt=media&token=198ba426-f4b0-4567-8680-7cd82b49b8ef" # TODO: replace with env vars

class ListingType(pydantic.BaseModel):
    """
    ListingType is a custom Pydantic type to capture
    property data exposed from the Kendal CRM XML Endpoint.

    Attributes
    ----------
    """
    kendal_ref: str
    title: str
    description: str
    offering_type: Union[Literal["Rent", "Off Plan", "Ready to Buy"]]
    pass

# GET XML FROM ENDPOINT
r = requests.get(KENDAL_ENDPOINT)
if r.status_code != 200:
    raise RuntimeError(f"XML Endpoint is unreachable. Status code {r.status_code} returned.")
# CONVERT XML TO DICT/JSON
dict_xml = xmltodict.parse(r.text)
print(dict_xml)
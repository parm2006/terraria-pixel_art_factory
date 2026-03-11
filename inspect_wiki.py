import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "TerrariaPixelArtTool/1.0"}
WIKI_BASE = "https://terraria.wiki.gg"

resp = requests.get(f"{WIKI_BASE}/wiki/Walls", headers=HEADERS)
soup = BeautifulSoup(resp.text, "html.parser")

subpages = []
for td in soup.find_all("td"):
    for a in td.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/wiki/") and ":" not in href:
            if href not in subpages:
                subpages.append(href)

print("Subpages linked from /wiki/Walls:")
for s in subpages:
    print(" ", s)
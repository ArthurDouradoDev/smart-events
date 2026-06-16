import os
import re
import urllib.request
import urllib.parse
from html.parser import HTMLParser

class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'script' and 'src' in attrs_dict:
            self.assets.append(('src', attrs_dict['src']))
        elif tag == 'link' and 'href' in attrs_dict:
            self.assets.append(('href', attrs_dict['href']))
        elif tag == 'img' and 'src' in attrs_dict:
            self.assets.append(('src', attrs_dict['src']))
        elif tag == 'source' and 'src' in attrs_dict:
            self.assets.append(('src', attrs_dict['src']))

def download_file(url, local_path):
    """Downloads a file, creating directories if necessary."""
    if os.path.exists(local_path):
        print(f"Already exists: {local_path}")
        return True
    
    # Create directory structure
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    
    print(f"Downloading: {url} -> {local_path}")
    try:
        # Use a user-agent to avoid blocking
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=10) as response, open(local_path, 'wb') as out_file:
            out_file.write(response.read())
        return True
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return False

def make_offline(html_source, base_url, output_dir):
    """
    Parses HTML source, downloads all relative/absolute assets,
    and updates the HTML to use local relative paths.
    """
    # 1. Parse HTML to find assets
    parser = AssetParser()
    parser.feed(html_source)
    
    updated_html = html_source
    
    # We sort assets by length of URL descending to avoid partial replacements
    sorted_assets = sorted(parser.assets, key=lambda x: len(x[1]), reverse=True)
    
    downloaded_count = 0
    failed_count = 0
    
    for attr_name, asset_url in sorted_assets:
        # Skip empty, data:URIs, anchor tags, or mailto/javascript links
        if not asset_url or asset_url.startswith('data:') or asset_url.startswith('#') or asset_url.startswith('javascript:'):
            continue
            
        # Resolve to absolute URL
        absolute_url = urllib.parse.urljoin(base_url, asset_url)
        parsed_url = urllib.parse.urlparse(absolute_url)
        
        # We only want http/https links
        if parsed_url.scheme not in ('http', 'https'):
            continue
            
        # Determine local path
        # If it's a relative path starting with / or simple name, clean it
        path = parsed_url.path
        if path.startswith('/'):
            path = path[1:]
            
        # If the path has no extension, it might be a dynamic page or query, we map it safely
        # Add host subfolder if we download from multiple places, or just preserve directory
        local_path = os.path.join(output_dir, path)
        
        # Download the asset
        success = download_file(absolute_url, local_path)
        if success:
            downloaded_count += 1
            # Replace the asset URL in the HTML with the local relative path
            # We want to replace exactly the quoted string in the HTML
            # e.g., href="/npsmart/css/bootstrap.min.css" -> href="npsmart/css/bootstrap.min.css"
            # Using simple string replace for the exact url attribute
            # To be safe, we replace occurrences of the URL
            # Relativize the path from the location of the HTML file
            # Since the HTML will be saved in the output_dir, the relative path is just `path`
            local_rel_url = path.replace('\\', '/')
            
            # Escape for regex if needed or do safe replacing
            # Replace in quotes to avoid partial replacements of other strings
            for quote in ('"', "'"):
                old_str = f'{attr_name}={quote}{asset_url}{quote}'
                new_str = f'{attr_name}={quote}{local_rel_url}{quote}'
                if old_str in updated_html:
                    updated_html = updated_html.replace(old_str, new_str)
                else:
                    # Fallback to direct replacement of url if it is not formatted with =quote
                    updated_html = updated_html.replace(asset_url, local_rel_url)
        else:
            failed_count += 1
            
    print(f"\nCompleted: {downloaded_count} downloaded, {failed_count} failed.")
    return updated_html

def main():
    # Configure parameters
    # The URL to download the HTML from
    target_url = "http://10.226.100.134/npsmart/map/earth_petal_leaflet"
    # Or, if you want to use the local HTML file already saved (earth_petal.html)
    use_local_file = True
    local_file_path = "earth_petal.html"
    
    # Base URL to resolve relative paths
    base_url = "http://10.226.100.134/"
    
    # Output directory
    output_dir = "offline_site"
    os.makedirs(output_dir, exist_ok=True)
    
    html_content = ""
    if use_local_file and os.path.exists(local_file_path):
        print(f"Reading local HTML file: {local_file_path}")
        with open(local_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            html_content = f.read()
    else:
        print(f"Fetching HTML from URL: {target_url}")
        try:
            req = urllib.request.Request(
                target_url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                html_content = response.read().decode('utf-8', errors='ignore')
            # Save raw HTML first
            with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write(html_content)
        except Exception as e:
            print(f"Failed to fetch base URL: {e}")
            return
            
    # Download assets and update HTML
    updated_html = make_offline(html_content, base_url, output_dir)
    
    # Save the updated HTML
    output_html_name = "index_offline.html" if use_local_file else "index.html"
    output_html_path = os.path.join(output_dir, output_html_name)
    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(updated_html)
        
    print(f"Offline HTML saved to: {output_html_path}")
    print("You can open this file in any browser to view the page offline!")

if __name__ == "__main__":
    main()

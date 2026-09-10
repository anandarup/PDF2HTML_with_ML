"""
Publish Module (OCI Object Storage)

Handles uploading HTML, media, and video files to OCI buckets
for the "Publish for Learners" workflow.

Uses the same OCI Instance Principal auth as the rest of the app.
"""

import os
import re
import logging
import mimetypes
from pathlib import Path
from bs4 import BeautifulSoup
from glossary_highlight import highlight_glossary_terms, GLOSSARY_CSS

_log = logging.getLogger(__name__)

# Bucket constants (matching the existing OCI setup)
HTML_BUCKET = "poc-interactivetxtbk1"
MEDIA_BUCKET = "poc-interactivetxt-media-src-bucket"
VIDEO_BUCKET = "poc-interactivetxt-media-dst-bucket"

# File extensions treated as streaming video
VIDEO_EXTENSIONS = {".mp4", ".m3u8", ".ts", ".webm", ".m4s"}

# Extensions to skip (not media, not video)
SKIP_EXTENSIONS = {".html", ".htm", ".json", ".txt", ".md", ".log"}

# Output directory (relative to this file's location)
OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "output"))

# OCI region
REGION = "ap-hyderabad-1"


def _get_oci_client():
    """Get OCI Object Storage client using Instance Principals."""
    import oci
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    client = oci.object_storage.ObjectStorageClient({}, signer=signer)
    namespace = client.get_namespace().data
    return client, namespace


def _get_public_url(namespace, bucket, object_name):
    """Construct the public OCI Object Storage URL.

    The object name is stored verbatim (it can contain spaces, em-dashes, and
    other characters from the job-dir/filename), but those characters are NOT
    valid in a URL path and must be percent-encoded — otherwise the browser
    can't fetch the object and media fails to load ("media could not be
    loaded / format not supported"). We encode each path segment while keeping
    the "/" separators intact.
    """
    from urllib.parse import quote

    encoded_object = quote(object_name, safe="/")
    return (
        f"https://objectstorage.{REGION}.oraclecloud.com"
        f"/n/{namespace}/b/{bucket}/o/{encoded_object}"
    )


def _upload_file(client, namespace, bucket, file_path, object_name):
    """Upload a single file to OCI Object Storage."""
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

    with open(file_path, "rb") as f:
        client.put_object(
            namespace,
            bucket,
            object_name,
            f,
            content_type=content_type,
        )
    _log.info(f"Uploaded {file_path} -> oci://{bucket}/{object_name}")


def _upload_bytes(client, namespace, bucket, data, object_name, content_type="text/html"):
    """Upload raw bytes to OCI Object Storage."""
    client.put_object(
        namespace,
        bucket,
        object_name,
        data,
        content_type=content_type,
    )
    _log.info(f"Uploaded bytes -> oci://{bucket}/{object_name}")


def _is_video_file(file_path):
    """Check if a file is a streaming video based on extension."""
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


def _is_skip_file(file_path):
    """Check if a file should be skipped (not media or video)."""
    return Path(file_path).suffix.lower() in SKIP_EXTENSIONS


def _strip_editor_ui(html_content):
    """
    Remove all editor-only UI elements from HTML for the learner view.

    Strips: Edit/Export/Publish buttons, formatting toolbar, all modals,
    and the inline editing JavaScript. Keeps the document content, TOC sidebar,
    document header, back-to-top button, and any CDN script tags (MathJax, Video.js, H5P).
    """

    # Remove the top-actions container (Edit/Export/Publish buttons wrapper).
    # It contains only <button> children (no nested <div>), so a single
    # non-greedy </div> is the correct close — matching </div>\s*</div> here
    # overruns into the following #editToolbar when its first child is not a
    # <div>.
    html_content = re.sub(
        r'<div[^>]*class="top-actions"[^>]*>.*?</div>',
        '', html_content, count=1, flags=re.DOTALL
    )

    # Remove the three action buttons (Edit, Export, Publish for Learners)
    html_content = re.sub(
        r'<button[^>]*id="editToggle"[^>]*>.*?</button>',
        '', html_content, flags=re.DOTALL
    )
    html_content = re.sub(
        r'<button[^>]*id="exportToggle"[^>]*>.*?</button>',
        '', html_content, flags=re.DOTALL
    )
    html_content = re.sub(
        r'<button[^>]*id="publishToggle"[^>]*>.*?</button>',
        '', html_content, flags=re.DOTALL
    )

    # Remove the entire edit toolbar div — match from its opening to the
    # Formula Input Dialog comment which immediately follows it.
    html_content = re.sub(
        r'<div[^>]*id="editToolbar"[^>]*role="toolbar"[^>]*>.*?(?=\s*<!-- Formula Input Dialog)',
        '', html_content, count=1, flags=re.DOTALL
    )

    # Remove all modal overlays and everything after them up to </body>.
    # The modals and inline <script> blocks are all editor-only and appear
    # between the last document element and </body>.
    html_content = re.sub(
        r'\s*<!-- Formula Input Dialog -->.*?</body>',
        '\n</body>', html_content, count=1, flags=re.DOTALL
    )

    # If the formula comment isn't present, try removing from first modal-overlay
    if 'modal-overlay' in html_content:
        html_content = re.sub(
            r'\s*<div[^>]*class="modal-overlay"[^>]*>.*?</body>',
            '\n</body>', html_content, count=1, flags=re.DOTALL
        )

    # Also remove the inline <script> that starts with "// Back to top"
    # (in case it appeared before the modals)
    html_content = re.sub(
        r'\s*<script>\s*// Back to top visibility.*?</script>',
        '', html_content, count=1, flags=re.DOTALL
    )

    # Remove any remaining contenteditable attributes
    html_content = re.sub(r'\s*contenteditable="[^"]*"', '', html_content)

    # Remove leftover editor drag affordances. draggable="true" on headings and
    # .section-media makes the browser start a native drag on mousedown, which
    # can swallow clicks on the media chips inside them.
    html_content = re.sub(r'\s*draggable="(?:true|false)"', '', html_content)

    # Remove block-controls that might have been left in
    html_content = re.sub(
        r'<div[^>]*class="block-controls"[^>]*>.*?</div>',
        '', html_content, flags=re.DOTALL
    )

    # Remove the edit-toolbar CSS class definitions (optional cleanup)
    html_content = re.sub(
        r'\.edit-toggle\s*\{[^}]*\}', '', html_content
    )
    html_content = re.sub(
        r'\.edit-toggle:hover\s*\{[^}]*\}', '', html_content
    )
    html_content = re.sub(
        r'\.publish-btn\s*\{[^}]*\}', '', html_content
    )
    html_content = re.sub(
        r'\.publish-btn:hover\s*\{[^}]*\}', '', html_content
    )

    # Pre-process chapter glossary terms server-side for learner view
    if 'chapter-glossary-data' in html_content:
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            glossary_el = soup.find('div', id='chapter-glossary-data')
            if glossary_el and glossary_el.get('data-entries'):
                entries_str = glossary_el.get('data-entries', '')
                entries = []
                for line in entries_str.strip().split('\n'):
                    if '|' in line:
                        parts = line.split('|', 1)
                        term = parts[0].strip()
                        definition = parts[1].strip()
                        if term and definition:
                            entries.append({'term': term, 'definition': definition})
                if entries:
                    html_content = highlight_glossary_terms(
                        html_content, entries, first_occurrence_only=False, max_highlights_per_term=0
                    )
        except Exception as e:
            _log.warning(f"Failed to pre-highlight glossary terms during publish: {e}")

    # Inject GLOSSARY_CSS into <head> if not already present
    if 'glossary-term' in html_content or 'chapter-glossary-data' in html_content:
        if 'glossary-term-styles' not in html_content and GLOSSARY_CSS not in html_content:
            style_tag = f'<style id="glossary-term-styles">\n{GLOSSARY_CSS}\n</style>'
            if '</head>' in html_content:
                html_content = html_content.replace('</head>', f'{style_tag}\n</head>', 1)
            else:
                html_content = style_tag + html_content

    # Inject the reader-shell CSS + theme bootstrap into <head>
    _head_inject = (
        READER_THEME_BOOTSTRAP
        + '\n<style id="reader-shell-styles">\n'
        + LEARNER_CSS
        + '\n</style>\n'
    )
    if '</head>' in html_content:
        html_content = html_content.replace('</head>', _head_inject + '</head>', 1)
    else:
        html_content = _head_inject + html_content

    # Inject the reader toolbar immediately after <body>
    html_content = re.sub(
        r'<body[^>]*>',
        lambda m: m.group(0) + '\n' + READER_TOOLBAR_HTML,
        html_content, count=1,
    )

    # Inject learner runtime + reader-shell scripts before </body>
    html_content = html_content.replace(
        '</body>', LEARNER_RUNTIME_SCRIPT + '\n' + READER_SHELL_SCRIPT + '\n</body>'
    )

    return html_content


# Minimal runtime JS for learner view — handles media popups, flip cards,
# accordions, tabs, animated headings, and back-to-top.
LEARNER_RUNTIME_SCRIPT = r'''<script>
(function(){
  // --- Back to top ---
  var btn=document.querySelector('.back-to-top');
  if(btn){window.addEventListener('scroll',function(){btn.classList.toggle('visible',window.scrollY>300)},{passive:true});}

  // --- Flip Card Deck Navigation ---
  window.flipDeckNav=function(deckId,dir){
    var deck=document.getElementById(deckId);if(!deck)return;
    var cards=deck.querySelectorAll('.flip-card');
    var counter=deck.querySelector('.card-counter');
    var curr=-1;
    cards.forEach(function(c,i){if(c.classList.contains('active'))curr=i;});
    if(curr<0)return;
    cards[curr].classList.remove('flipped','active');
    var next=(curr+dir+cards.length)%cards.length;
    cards[next].classList.add('active');
    if(counter)counter.textContent=(next+1)+' / '+cards.length;
  };

  // --- Accordion Toggle ---
  document.querySelectorAll('.accordion').forEach(function(acc){
    acc.addEventListener('click',function(e){
      var h=e.target.closest('.accordion-header');if(!h)return;
      e.preventDefault();
      var item=h.closest('.accordion-item');
      var open=item.classList.contains('open');
      item.classList.toggle('open');
      h.setAttribute('aria-expanded',open?'false':'true');
    });
  });

  // --- Horizontal Tabs ---
  document.querySelectorAll('.htabs').forEach(function(c){
    var navs=c.querySelectorAll('.htabs-nav-item'),panels=c.querySelectorAll('.htabs-panel');
    function activate(i){navs.forEach(function(n,j){n.setAttribute('aria-selected',j===i?'true':'false');n.setAttribute('tabindex',j===i?'0':'-1');});panels.forEach(function(p,j){p.setAttribute('aria-hidden',j===i?'false':'true');});}
    navs.forEach(function(n,i){n.addEventListener('click',function(e){e.preventDefault();activate(i);n.focus();});n.addEventListener('keydown',function(e){var idx=i,len=navs.length;if(e.key==='ArrowRight')idx=(i+1)%len;else if(e.key==='ArrowLeft')idx=(i-1+len)%len;else if(e.key==='Home')idx=0;else if(e.key==='End')idx=len-1;else return;e.preventDefault();activate(idx);navs[idx].focus();});});
  });

  // --- Vertical Tabs ---
  document.querySelectorAll('.vtabs').forEach(function(c){
    var navs=c.querySelectorAll('.vtabs-nav-item'),panels=c.querySelectorAll('.vtabs-panel');
    function activate(i){navs.forEach(function(n,j){n.setAttribute('aria-selected',j===i?'true':'false');n.setAttribute('tabindex',j===i?'0':'-1');});panels.forEach(function(p,j){p.setAttribute('aria-hidden',j===i?'false':'true');});}
    navs.forEach(function(n,i){n.addEventListener('click',function(e){e.preventDefault();activate(i);n.focus();});n.addEventListener('keydown',function(e){var idx=i,len=navs.length;if(e.key==='ArrowDown')idx=(i+1)%len;else if(e.key==='ArrowUp')idx=(i-1+len)%len;else if(e.key==='Home')idx=0;else if(e.key==='End')idx=len-1;else return;e.preventDefault();activate(idx);navs[idx].focus();});});
  });

  // --- Animated Heading Word Cycling ---
  document.querySelectorAll('.anim-heading').forEach(function(c){
    var words=c.querySelectorAll('.anim-heading-word');if(words.length<2)return;
    var curr=0;
    setInterval(function(){
      if(!document.body.contains(c))return;
      if(window.matchMedia('(prefers-reduced-motion:reduce)').matches)return;
      var prev=curr;curr=(curr+1)%words.length;
      words[prev].classList.remove('active');words[prev].classList.add('exiting');
      setTimeout(function(){words[prev].classList.remove('exiting');},400);
      words[curr].classList.add('active');
    },2500);
  });

  // --- Media Popup ---
  var popup=document.getElementById('mediaPopup');
  if(!popup){
    // Create media popup if not present (stripped during publish)
    popup=document.createElement('div');
    popup.className='media-popup-overlay';popup.id='mediaPopup';
    popup.setAttribute('role','dialog');popup.setAttribute('aria-modal','true');
    popup.innerHTML='<div class="media-popup"><div class="media-popup-header"><h3 id="mediaPopupTitle"></h3><button type="button" class="media-popup-close" id="mediaPopupClose" aria-label="Close">&times;</button></div><div class="media-popup-body" id="mediaPopupBody"></div></div>';
    document.body.appendChild(popup);
  }

  function getEmbedUrl(url){
    var m=url.match(/(?:youtube\\.com\\/watch\\?v=|youtu\\.be\\/|youtube\\.com\\/embed\\/|youtube\\.com\\/shorts\\/|youtube\\.com\\/live\\/)([a-zA-Z0-9_-]{11})/);
    if(m)return'https://www.youtube.com/embed/'+m[1];
    m=url.match(/youtube\\.com\\/.*[?&]v=([a-zA-Z0-9_-]{11})/);
    if(m)return'https://www.youtube.com/embed/'+m[1];
    m=url.match(/vimeo\\.com\\/(\\d+)/);
    if(m)return'https://player.vimeo.com/video/'+m[1];
    m=url.match(/drive\\.google\\.com\\/file\\/d\\/([^/]+)/);
    if(m)return'https://drive.google.com/file/d/'+m[1]+'/preview';
    m=url.match(/loom\\.com\\/share\\/([a-zA-Z0-9]+)/);
    if(m)return'https://www.loom.com/embed/'+m[1];
    return null;
  }

  function escAttr(s){return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');}

  // Wire all media-icon buttons
  document.querySelectorAll('.media-icon.has-content').forEach(function(btn){
    btn.addEventListener('click',function(e){
      e.stopPropagation();
      var src=btn.getAttribute('data-media-src');
      var type=btn.getAttribute('data-media-type');
      if(!src)return;
      var title=document.getElementById('mediaPopupTitle');
      var body=document.getElementById('mediaPopupBody');
      title.textContent=type.charAt(0).toUpperCase()+type.slice(1);
      var content='';
      switch(type){
        case'video':
          var vid='vp-'+Date.now();
          content='<video id="'+vid+'" class="video-js vjs-big-play-centered vjs-fluid" controls preload="auto"><source src="'+escAttr(src)+'" type="video/mp4"/></video>';
          break;
        case'audio':
          content='<audio controls autoplay src="'+escAttr(src)+'" style="width:100%;">Audio not supported.</audio>';
          break;
        case'pptx':
          if(src.toLowerCase().endsWith('.pdf'))content='<iframe src="'+escAttr(src)+'" style="width:100%;min-height:500px;border:none;"></iframe>';
          else content='<div style="text-align:center;padding:2rem;"><a href="'+escAttr(src)+'" download style="padding:0.75rem 1.5rem;background:#0f4c75;color:#fff;border-radius:8px;text-decoration:none;">Download Presentation</a></div>';
          break;
        case'h5p':
          if(src.startsWith('http://')||src.startsWith('https://')){
            content='<iframe src="'+escAttr(src)+'" style="width:100%;min-height:500px;border:none;border-radius:6px;" allowfullscreen></iframe><p style="margin-top:0.5rem;text-align:center;font-size:0.8rem;"><a href="'+escAttr(src)+'" target="_blank" rel="noopener">Open in new tab</a></p>';
          } else {
            var hid='h5p-'+Date.now();
            content='<div id="'+hid+'" style="min-height:400px;"></div>';
            setTimeout(function(){var el=document.getElementById(hid);if(el&&window.H5PStandalone)new H5PStandalone.H5P(el,{h5pJsonPath:src,frameJs:'https://unpkg.com/h5p-standalone@3.8.0/dist/frame.bundle.js',frameCss:'https://unpkg.com/h5p-standalone@3.8.0/dist/styles/h5p.css'});else if(el){el.innerHTML='<iframe src="'+escAttr(src)+'" style="width:100%;min-height:500px;border:none;" allowfullscreen></iframe>';}},100);
          }
          break;
        case'glossary':
          var parts=src.split('|');
          content='<div style="font-size:1.2rem;font-weight:700;margin-bottom:0.5rem;">'+(parts[0]||'').trim()+'</div><div style="line-height:1.6;">'+(parts[1]||src).trim()+'</div>';
          break;
        case'url':
          var embed=getEmbedUrl(src);
          if(embed)content='<iframe src="'+escAttr(embed)+'" allowfullscreen style="width:100%;min-height:450px;border:none;"></iframe>';
          else content='<div style="text-align:center;padding:2rem;"><a href="'+escAttr(src)+'" target="_blank" rel="noopener" style="padding:0.75rem 1.5rem;background:#0f4c75;color:#fff;border-radius:8px;text-decoration:none;">Open in new tab</a></div>';
          break;
        default:
          content='<a href="'+escAttr(src)+'" target="_blank" rel="noopener">Open file</a>';
      }
      body.innerHTML=content;
      popup.classList.add('visible');
      // Init Video.js
      var vjsEl=body.querySelector('.video-js');
      var player=null;
      if(vjsEl&&window.videojs)player=videojs(vjsEl.id);
      // Close
      function close(){popup.classList.remove('visible');if(player){player.dispose();player=null;}body.innerHTML='';}
      document.getElementById('mediaPopupClose').onclick=close;
      popup.addEventListener('click',function handler(ev){if(ev.target===popup){close();popup.removeEventListener('click',handler);}});
    });
  });

  // --- Escape key to close popup ---
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape'&&popup.classList.contains('visible')){
      popup.classList.remove('visible');
      document.getElementById('mediaPopupBody').innerHTML='';
    }
  });

  // --- scrollToSection postMessage handler (for CMS iframe integration) ---
  window.addEventListener('message',function(e){
    if(e.data&&e.data.type==='scrollToSection'&&e.data.sectionId){
      var el=document.getElementById(e.data.sectionId);
      if(el)el.scrollIntoView({behavior:'smooth',block:'start'});
    }
    // --- Glossary highlighting via postMessage ---
    if(e.data&&e.data.type==='applyGlossary'&&e.data.glossary){
      var root=document.querySelector('.document-body')||document.body;
      if(window.highlightGlossary){
        window.highlightGlossary(root,e.data.glossary,e.data.options||{});
      } else {
        // Inline minimal TreeWalker highlighter (if external script not loaded)
        (function(rt,terms){
          var SKIP={SCRIPT:1,STYLE:1,CODE:1,PRE:1,DFN:1,A:1,BUTTON:1,H1:1,H2:1,H3:1};
          terms=terms.filter(function(g){return g.term&&g.definition&&g.term.length>=2;}).sort(function(a,b){return b.term.length-a.term.length;});
          if(!terms.length)return;
          var esc=terms.map(function(g){return g.term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');});
          var pat=new RegExp('\\\\b('+esc.join('|')+')\\\\b','gi');
          var map={};terms.forEach(function(g){map[g.term.toLowerCase()]=g;});
          var counts={};terms.forEach(function(g){counts[g.term.toLowerCase()]=0;});
          var w=document.createTreeWalker(rt,NodeFilter.SHOW_TEXT,{acceptNode:function(n){if(!n.nodeValue||!n.nodeValue.trim())return NodeFilter.FILTER_REJECT;var p=n.parentElement;while(p&&p!==rt){if(SKIP[p.tagName]||p.classList.contains('glossary-term'))return NodeFilter.FILTER_REJECT;p=p.parentElement;}return NodeFilter.FILTER_ACCEPT;}});
          var nodes=[];var nd;while((nd=w.nextNode()))nodes.push(nd);
          var reps=[];
          for(var i=0;i<nodes.length;i++){
            var txt=nodes[i].nodeValue;pat.lastIndex=0;
            if(!pat.test(txt))continue;
            pat.lastIndex=0;var frag=document.createDocumentFragment();var li=0;var m;var has=false;
            while((m=pat.exec(txt))!==null){
              var tl=m[0].toLowerCase();if(counts[tl]>=1)continue;counts[tl]++;has=true;
              if(m.index>li)frag.appendChild(document.createTextNode(txt.slice(li,m.index)));
              var dfn=document.createElement('dfn');dfn.className='glossary-term';dfn.tabIndex=0;dfn.setAttribute('role','term');
              dfn.setAttribute('data-definition',map[tl].definition);dfn.title=map[tl].definition;
              dfn.setAttribute('aria-label',m[0]+': '+map[tl].definition);dfn.textContent=m[0];
              frag.appendChild(dfn);li=m.index+m[0].length;
            }
            if(!has)continue;
            if(li<txt.length)frag.appendChild(document.createTextNode(txt.slice(li)));
            reps.push({n:nodes[i],f:frag});
          }
          for(var j=0;j<reps.length;j++)reps[j].n.parentNode.replaceChild(reps[j].f,reps[j].n);
          if(!document.getElementById('glossary-term-styles')){var s=document.createElement('style');s.id='glossary-term-styles';s.textContent='.glossary-term{font-style:normal;border-bottom:2px dotted #3282b8;cursor:help;position:relative;display:inline;}.glossary-term:hover,.glossary-term:focus{background:#eff6ff;border-bottom-color:#0f4c75;outline:none;}.glossary-term:hover::after,.glossary-term:focus::after{content:attr(data-definition);position:absolute;bottom:calc(100% + 8px);left:0;min-width:200px;max-width:300px;padding:0.6rem 0.8rem;background:#1a1a2e;color:#f0f0f0;border-radius:8px;font-size:0.8rem;line-height:1.4;white-space:normal;z-index:100;box-shadow:0 4px 16px rgba(0,0,0,0.2);pointer-events:none;}.glossary-term:hover::before,.glossary-term:focus::before{content:"";position:absolute;bottom:calc(100% + 2px);left:16px;border:6px solid transparent;border-top-color:#1a1a2e;z-index:101;}';document.head.appendChild(s);}
        })(root,e.data.glossary);
      }
    }
  });

  // --- Auto-apply chapter glossary on page load (from stored data) ---
  (function(){
    var glossaryEl=document.querySelector('#chapter-glossary-data');
    if(!glossaryEl)return;
    var entries=glossaryEl.getAttribute('data-entries');
    if(!entries||!entries.trim())return;
    var terms=entries.split('\n').map(function(line){
      var parts=line.split('|');
      return {term:(parts[0]||'').trim(),definition:(parts[1]||'').trim()};
    }).filter(function(e){return e.term&&e.definition&&e.term.length>=2;});
    if(!terms.length)return;
    var root=document.querySelector('.document-body')||document.body;
    // Use the same TreeWalker highlighter
    var SKIP={SCRIPT:1,STYLE:1,CODE:1,PRE:1,DFN:1,A:1,BUTTON:1,H1:1,H2:1,H3:1};
    terms.sort(function(a,b){return b.term.length-a.term.length;});
    var esc=terms.map(function(g){return g.term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');});
    var pat=new RegExp('\\\\b('+esc.join('|')+')\\\\b','gi');
    var map={};terms.forEach(function(g){map[g.term.toLowerCase()]=g;});
    var counts={};terms.forEach(function(g){counts[g.term.toLowerCase()]=0;});
    var w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT,{acceptNode:function(n){if(!n.nodeValue||!n.nodeValue.trim())return NodeFilter.FILTER_REJECT;var p=n.parentElement;while(p&&p!==root){if(SKIP[p.tagName]||p.classList.contains('glossary-term')||(p.id==='chapter-glossary-data'))return NodeFilter.FILTER_REJECT;p=p.parentElement;}return NodeFilter.FILTER_ACCEPT;}});
    var nodes=[];var nd;while((nd=w.nextNode()))nodes.push(nd);
    var reps=[];
    for(var i=0;i<nodes.length;i++){
      var txt=nodes[i].nodeValue;pat.lastIndex=0;
      if(!pat.test(txt))continue;
      pat.lastIndex=0;var frag=document.createDocumentFragment();var li=0;var m;var has=false;
      while((m=pat.exec(txt))!==null){
        var tl=m[0].toLowerCase();counts[tl]++;has=true;
        if(m.index>li)frag.appendChild(document.createTextNode(txt.slice(li,m.index)));
        var dfn=document.createElement('dfn');dfn.className='glossary-term';dfn.tabIndex=0;dfn.setAttribute('role','term');
        dfn.setAttribute('data-definition',map[tl].definition);dfn.title=map[tl].definition;
        dfn.setAttribute('aria-label',m[0]+': '+map[tl].definition);dfn.textContent=m[0];
        frag.appendChild(dfn);li=m.index+m[0].length;
      }
      if(!has)continue;
      if(li<txt.length)frag.appendChild(document.createTextNode(txt.slice(li)));
      reps.push({n:nodes[i],f:frag});
    }
    for(var j=0;j<reps.length;j++)reps[j].n.parentNode.replaceChild(reps[j].f,reps[j].n);
    // Inject CSS
    if(!document.getElementById('glossary-term-styles')){var s=document.createElement('style');s.id='glossary-term-styles';s.textContent='.glossary-term{font-style:normal;border-bottom:2px dotted #3282b8;cursor:help;position:relative;display:inline;}.glossary-term:hover,.glossary-term:focus{background:#eff6ff;border-bottom-color:#0f4c75;outline:none;}.glossary-term:hover::after,.glossary-term:focus::after{content:attr(data-definition);position:absolute;bottom:calc(100% + 8px);left:0;min-width:200px;max-width:300px;padding:0.6rem 0.8rem;background:#1a1a2e;color:#f0f0f0;border-radius:8px;font-size:0.8rem;line-height:1.4;white-space:normal;z-index:100;box-shadow:0 4px 16px rgba(0,0,0,0.2);pointer-events:none;}.glossary-term:hover::before,.glossary-term:focus::before{content:"";position:absolute;bottom:calc(100% + 2px);left:16px;border:6px solid transparent;border-top-color:#1a1a2e;z-index:101;}';document.head.appendChild(s);}
  })();

  // --- Learner Notes (localStorage-based, works offline) ---
  (function(){
    var NOTES_KEY='docling_notes_'+encodeURIComponent(window.location.pathname).replace(/[^a-zA-Z0-9]/g,'').substring(0,40);
    function loadNotes(){try{return JSON.parse(localStorage.getItem(NOTES_KEY))||{};}catch(e){return {};}}
    function saveNotes(notes){localStorage.setItem(NOTES_KEY,JSON.stringify(notes));}

    // Inject notes CSS
    var style=document.createElement('style');
    style.textContent='.note-btn{position:absolute;right:-40px;top:2px;width:30px;height:30px;border:none;border-radius:50%;background:#eff6ff;cursor:pointer;font-size:14px;display:flex;align-items:center;justify-content:center;opacity:0.6;transition:opacity 0.2s,transform 0.15s;box-shadow:0 1px 4px rgba(0,0,0,0.1);z-index:5;}.note-btn:hover{opacity:1;transform:scale(1.1);}.note-btn.has-note{opacity:1;background:#dbeafe;}.note-panel{display:none;position:absolute;right:-320px;top:0;width:280px;background:#fff;border:1px solid #e0e0e0;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.12);padding:10px;z-index:20;font-size:0.85rem;}.note-panel.visible{display:block;}.note-panel textarea{width:100%;min-height:80px;border:1px solid #e0e0e0;border-radius:6px;padding:8px;font-size:0.85rem;font-family:inherit;resize:vertical;}.note-panel-actions{display:flex;gap:6px;margin-top:8px;justify-content:flex-end;}.note-panel-actions button{padding:4px 10px;border:1px solid #e0e0e0;border-radius:4px;cursor:pointer;font-size:0.75rem;background:#fff;}.note-panel-actions .note-del{color:#ef4444;border-color:#ef4444;}.note-panel-actions .note-del:hover{background:#fef2f2;}@media(max-width:900px){.note-btn{right:4px;top:-28px;}.note-panel{right:0;top:32px;width:240px;}}';
    document.head.appendChild(style);

    var notes=loadNotes();

    // Add note buttons to each heading with an id
    document.querySelectorAll('h1[id],h2[id],h3[id]').forEach(function(h){
      h.style.position='relative';
      var btn=document.createElement('button');
      btn.className='note-btn'+(notes[h.id]?' has-note':'');
      btn.title=notes[h.id]?'View/edit note':'Add note';
      btn.textContent=notes[h.id]?'\\u270F':'\\u2795';
      btn.setAttribute('aria-label',notes[h.id]?'Edit note for: '+h.textContent.trim().substring(0,30):'Add note to: '+h.textContent.trim().substring(0,30));

      var panel=document.createElement('div');
      panel.className='note-panel';
      panel.innerHTML='<textarea placeholder="Write your note here...">'+(notes[h.id]||'').replace(/</g,'&lt;')+'</textarea><div class="note-panel-actions"><button class="note-del" title="Delete note">Delete</button><button class="note-save" title="Save note">Save</button></div>';

      var open=false;
      btn.addEventListener('click',function(e){
        e.stopPropagation();
        open=!open;
        panel.classList.toggle('visible',open);
        if(open)panel.querySelector('textarea').focus();
      });

      panel.querySelector('.note-save').addEventListener('click',function(){
        var text=panel.querySelector('textarea').value.trim();
        if(text){
          notes[h.id]=text;
          btn.className='note-btn has-note';
          btn.textContent='\\u270F';
          btn.title='View/edit note';
        }else{
          delete notes[h.id];
          btn.className='note-btn';
          btn.textContent='\\u2795';
          btn.title='Add note';
        }
        saveNotes(notes);
        open=false;panel.classList.remove('visible');
      });

      panel.querySelector('.note-del').addEventListener('click',function(){
        delete notes[h.id];
        saveNotes(notes);
        panel.querySelector('textarea').value='';
        btn.className='note-btn';
        btn.textContent='\\u2795';
        btn.title='Add note';
        open=false;panel.classList.remove('visible');
      });

      // Close panel when clicking outside
      document.addEventListener('click',function(e){
        if(open&&!panel.contains(e.target)&&e.target!==btn){
          open=false;panel.classList.remove('visible');
        }
      });

      h.appendChild(btn);
      h.appendChild(panel);
    });

    // Report section positions to parent (for CMS integration)
    function reportSectionPositions(){
      var headings=document.querySelectorAll('h1[id],h2[id],h3[id]');
      var positions=[];
      headings.forEach(function(h){var r=h.getBoundingClientRect();positions.push({id:h.id,top:r.top+window.scrollY});});
      if(window.parent!==window)window.parent.postMessage({type:'sectionPositions',positions:positions},'*');
    }
    reportSectionPositions();
    var _spT;window.addEventListener('scroll',function(){clearTimeout(_spT);_spT=setTimeout(reportSectionPositions,300);},{passive:true});
  })();

  // --- Bookmark / Reading Position (localStorage, offline) ---
  (function(){
    try{
    var BM_KEY='docling_bookmark_'+encodeURIComponent(window.location.pathname).replace(/[^a-zA-Z0-9]/g,'').substring(0,40);

    function getBookmark(){try{return JSON.parse(localStorage.getItem(BM_KEY));}catch(e){return null;}}
    function saveBookmark(data){localStorage.setItem(BM_KEY,JSON.stringify(data));}
    function clearBookmark(){localStorage.removeItem(BM_KEY);}

    // Find the nearest heading above the current scroll position
    function getCurrentSection(){
      var headings=document.querySelectorAll('h1[id],h2[id],h3[id]');
      var current=null;
      var scrollTop=window.scrollY||document.documentElement.scrollTop;
      headings.forEach(function(h){
        if(h.getBoundingClientRect().top+window.scrollY<=scrollTop+100){
          current=h;
        }
      });
      return current;
    }

    // Inject bookmark CSS
    var bmStyle=document.createElement('style');
    bmStyle.textContent='.bm-bar{position:fixed;bottom:1rem;right:1rem;z-index:999;display:flex;gap:6px;align-items:center;}.bm-btn{padding:8px 14px;border:none;border-radius:8px;cursor:pointer;font-size:0.8rem;font-weight:500;box-shadow:0 2px 8px rgba(0,0,0,0.12);transition:transform 0.15s,background 0.15s;}.bm-btn:hover{transform:scale(1.05);}.bm-save{background:#3b82f6;color:#fff;}.bm-save:hover{background:#2563eb;}.bm-save.saved{background:#10b981;}.bm-resume{background:#f59e0b;color:#fff;}.bm-resume:hover{background:#d97706;}.bm-clear{background:#fff;color:#6b7280;border:1px solid #e5e7eb;font-size:0.7rem;padding:6px 10px;}.bm-toast{position:fixed;bottom:4rem;right:1rem;background:#1f2937;color:#fff;padding:8px 16px;border-radius:6px;font-size:0.8rem;opacity:0;transition:opacity 0.3s;z-index:1000;pointer-events:none;}.bm-toast.visible{opacity:1;}';
    document.head.appendChild(bmStyle);

    // Create bookmark bar
    var bar=document.createElement('div');
    bar.className='bm-bar';

    var saveBtn=document.createElement('button');
    saveBtn.className='bm-btn bm-save';
    saveBtn.textContent='\\uD83D\\uDD16 Save Position';
    saveBtn.title='Bookmark your current reading position';

    var resumeBtn=document.createElement('button');
    resumeBtn.className='bm-btn bm-resume';
    resumeBtn.textContent='\\u25B6 Resume Reading';
    resumeBtn.title='Jump back to your saved position';
    resumeBtn.style.display='none';

    var clearBtn=document.createElement('button');
    clearBtn.className='bm-btn bm-clear';
    clearBtn.textContent='\\u2716';
    clearBtn.title='Clear bookmark';
    clearBtn.style.display='none';

    var toast=document.createElement('div');
    toast.className='bm-toast';
    document.body.appendChild(toast);

    function showToast(msg){
      toast.textContent=msg;
      toast.classList.add('visible');
      setTimeout(function(){toast.classList.remove('visible');},2000);
    }

    // Check for existing bookmark on load
    var existing=getBookmark();
    if(existing&&existing.sectionId){
      resumeBtn.style.display='';
      clearBtn.style.display='';
    }

    // Save position
    saveBtn.addEventListener('click',function(){
      var section=getCurrentSection();
      if(!section){showToast('Scroll down a bit first');return;}
      var scrollPct=Math.round((window.scrollY/(document.documentElement.scrollHeight-window.innerHeight))*100);
      // Get clean title (exclude note buttons and other injected elements)
      var titleClone=section.cloneNode(true);
      var btns=titleClone.querySelectorAll('button,.note-btn,.note-panel');
      btns.forEach(function(b){b.remove();});
      var cleanTitle=titleClone.textContent.trim().substring(0,60);
      saveBookmark({
        sectionId:section.id,
        sectionTitle:cleanTitle,
        scrollPercent:scrollPct,
        timestamp:new Date().toISOString()
      });
      saveBtn.textContent='\\u2705 Saved!';
      saveBtn.className='bm-btn bm-save saved';
      setTimeout(function(){saveBtn.textContent='\\uD83D\\uDD16 Save Position';saveBtn.className='bm-btn bm-save';},1500);
      resumeBtn.style.display='';
      clearBtn.style.display='';
      showToast('Position saved: '+section.textContent.trim().substring(0,40));
    });

    // Resume reading
    resumeBtn.addEventListener('click',function(){
      var bm=getBookmark();
      if(!bm||!bm.sectionId)return;
      var el=document.getElementById(bm.sectionId);
      if(el){
        el.scrollIntoView({behavior:'smooth',block:'start'});
        // Flash the heading briefly
        el.style.transition='background 0.3s';
        el.style.background='#fef08a';
        setTimeout(function(){el.style.background='';},1500);
        showToast('Resumed: '+bm.sectionTitle);
      }else{
        showToast('Section not found (may have been renamed)');
      }
    });

    // Clear bookmark
    clearBtn.addEventListener('click',function(){
      clearBookmark();
      resumeBtn.style.display='none';
      clearBtn.style.display='none';
      showToast('Bookmark cleared');
    });

    bar.appendChild(saveBtn);
    bar.appendChild(resumeBtn);
    bar.appendChild(clearBtn);
    document.body.appendChild(bar);

    // Auto-save reading position on scroll (debounced, every 10s of reading)
    var autoSaveTimer;
    window.addEventListener('scroll',function(){
      clearTimeout(autoSaveTimer);
      autoSaveTimer=setTimeout(function(){
        var section=getCurrentSection();
        if(section){
          var scrollPct=Math.round((window.scrollY/(document.documentElement.scrollHeight-window.innerHeight))*100);
          saveBookmark({
            sectionId:section.id,
            sectionTitle:section.textContent.trim().substring(0,60),
            scrollPercent:scrollPct,
            timestamp:new Date().toISOString()
          });
          resumeBtn.style.display='';
          clearBtn.style.display='';
        }
      },10000); // Auto-save after 10s of no scroll
    },{passive:true});

    // Auto-resume: if bookmark exists and user just opened the page, scroll there
    if(existing&&existing.sectionId){
      setTimeout(function(){
        var el=document.getElementById(existing.sectionId);
        if(el){
          el.scrollIntoView({behavior:'smooth',block:'start'});
          el.style.transition='background 0.3s';
          el.style.background='#fef08a';
          setTimeout(function(){el.style.background='';},2000);
          showToast('Resumed reading: '+existing.sectionTitle);
        }else{
          showToast('Bookmark: '+existing.sectionTitle+' (section not found)');
        }
      },800);
    }

    // Notify parent frame about bookmark state
    if(window.parent!==window){
      window.parent.postMessage({type:'bookmarkState',bookmark:existing},'*');
    }
    }catch(err){console.warn('Bookmark init error:',err);}
  })();
})();
</script>'''


# ---------------------------------------------------------------------------
# Reader shell — Apple-style learner reading view.
# Injected at publish: a fixed reader toolbar (Contents / text size / font /
# light-dark / notes / bookmark), a Contents rail with reading progress,
# section cards, and a full dark theme. All additive; the editor template
# is not touched.
# ---------------------------------------------------------------------------

READER_THEME_BOOTSTRAP = (
    "<script>(function(){try{var m=localStorage.getItem('rd-theme')||'auto';"
    "var d=m==='dark'||(m!=='light'&&window.matchMedia('(prefers-color-scheme:dark)').matches);"
    "document.documentElement.setAttribute('data-rd-theme',d?'dark':'light');"
    "var s=parseFloat(localStorage.getItem('rd-scale'));"
    "if(s>0)document.documentElement.style.setProperty('--rd-scale',s);}catch(e){}})();</script>"
)

LEARNER_CSS = r'''
/* ===== Reader shell ===== */
:root{
  --rd-scale:1;
  --rd-font:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --rd-serif:"New York","Iowan Old Style","Palatino Linotype",Georgia,"Times New Roman",serif;
  --rd-bg:#f3f3f6; --rd-surface:#fff; --rd-surface-2:#f6f6f8;
  --rd-ink:#1d1d1f; --rd-ink-2:#55555c; --rd-ink-3:#86868b;
  --rd-hairline:rgba(0,0,0,.09); --rd-hairline-2:rgba(0,0,0,.14);
  --rd-fill:rgba(120,120,132,.10); --rd-fill-2:rgba(120,120,132,.20);
  --rd-accent:#0a84ff; --rd-accent-ink:#0a68d0; --rd-tint:rgba(10,132,255,.12);
  --rd-sidebar-bg:#eeeef1; --rd-material:rgba(243,243,246,.72);
  --rd-shadow-card:0 1px 2px rgba(0,0,0,.04),0 8px 24px -14px rgba(0,0,0,.14);
  --rd-shadow-pop:0 12px 40px -8px rgba(0,0,0,.24);
  --rd-node:#c7c7cc; --rd-done:#34c759;
}
html[data-rd-theme="dark"]{
  --rd-bg:#0a0a0b; --rd-surface:#161618; --rd-surface-2:#1e1e21;
  --rd-ink:#f5f5f7; --rd-ink-2:#a8a8ae; --rd-ink-3:#7c7c83;
  --rd-hairline:rgba(255,255,255,.11); --rd-hairline-2:rgba(255,255,255,.18);
  --rd-fill:rgba(255,255,255,.08); --rd-fill-2:rgba(255,255,255,.16);
  --rd-accent:#4c9dff; --rd-accent-ink:#7fb8ff; --rd-tint:rgba(76,157,255,.18);
  --rd-sidebar-bg:#0e0e10; --rd-material:rgba(22,22,24,.74);
  --rd-shadow-card:0 1px 2px rgba(0,0,0,.4),0 10px 30px -16px rgba(0,0,0,.7);
  --rd-shadow-pop:0 12px 40px -8px rgba(0,0,0,.6);
  --rd-node:#48484c; --rd-done:#30d158;
}
/* re-point the editor template tokens at the reader palette so every
   existing rule (.content-box, figures, tables, code…) follows the theme */
:root{
  --color-bg:var(--rd-surface); --color-text:var(--rd-ink);
  --color-text-secondary:var(--rd-ink-2); --color-heading:var(--rd-ink);
  --color-border:var(--rd-hairline); --color-code-bg:var(--rd-surface-2);
  --color-link:var(--rd-accent); --color-link-hover:var(--rd-accent-ink);
  --color-toc-bg:var(--rd-sidebar-bg); --color-toc-active:var(--rd-accent);
}
*{-webkit-tap-highlight-color:transparent;}
html{scroll-padding-top:70px;}
body{background:var(--rd-bg);color:var(--rd-ink);font-family:var(--rd-font);-webkit-font-smoothing:antialiased;}

.document-wrapper{padding-top:52px;background:var(--rd-bg);}
.content{
  max-width:44rem; margin:0 auto;
  padding:clamp(1.25rem,4vw,2.5rem) clamp(1rem,4vw,2rem) 6rem;
  font-size:calc(1.0625rem*var(--rd-scale));
}
.document-header{border:0;margin:0 0 1.25rem;padding:0 clamp(.2rem,2vw,.6rem);}
.document-title{
  font-family:var(--rd-font); font-weight:800; letter-spacing:-.03em;
  font-size:clamp(2rem,6vw,3rem); line-height:1.05; text-wrap:balance; color:var(--rd-ink);
}
.document-meta{display:none;}

/* section cards (wrapped by the shell script) */
.rd-card{
  background:var(--rd-surface); border:1px solid var(--rd-hairline);
  border-radius:18px; padding:clamp(1.4rem,4vw,2.6rem); margin:0 0 1.1rem;
  box-shadow:var(--rd-shadow-card); scroll-margin-top:70px;
}
.rd-card>:first-child{margin-top:0!important;}
.rd-card>:last-child{margin-bottom:0!important;}

/* typography */
.document-body{line-height:1.72;color:var(--rd-ink);font-size:1em;}
.document-body[data-rd-serif]{font-family:var(--rd-serif);}
.document-body[data-rd-serif] p{line-height:1.78;}
.document-body p{margin:0 0 1.05em;text-wrap:pretty;}
.document-body h1,.document-body h2,.document-body h3,.document-body h4{
  font-family:var(--rd-font); color:var(--rd-ink); font-weight:700;
  letter-spacing:-.021em; line-height:1.22; text-wrap:balance; margin:1.7em 0 .6em;
}
.rd-card>h1:first-child,.rd-card>h2:first-child,.rd-card>h3:first-child{margin-top:0;}
.document-body h2{font-size:calc(1.5rem*var(--rd-scale));}
.document-body h3{font-size:calc(1.18rem*var(--rd-scale));}
.document-body h4{font-size:calc(1rem*var(--rd-scale));color:var(--rd-ink-2);}
.document-body a{color:var(--rd-accent);text-underline-offset:2px;}
.document-body ul,.document-body ol{margin:0 0 1.05em;padding-left:1.35em;}
.document-body li{margin:.3em 0;}
.document-body strong{font-weight:650;}
.document-body img{max-width:100%;height:auto;border-radius:12px;}
.document-body figure,.document-body .figure-wrap{margin:1.4em 0;padding:0;float:none!important;max-width:100%!important;}
.document-body figure img,.document-body .figure-wrap img{border:1px solid var(--rd-hairline);background:var(--rd-surface-2);}
.document-body figcaption{margin-top:.5rem;font-size:.82em;color:var(--rd-ink-3);text-align:center;}
.document-body blockquote{margin:1.3em 0;padding:.2em 0 .2em 1.1em;border-left:3px solid var(--rd-hairline-2);color:var(--rd-ink-2);}
.document-body pre{background:var(--rd-surface-2);border:1px solid var(--rd-hairline);border-radius:12px;padding:1rem 1.1rem;overflow-x:auto;font-size:.9em;}
.document-body table{width:100%;border-collapse:collapse;margin:1.3em 0;font-size:.95em;display:block;overflow-x:auto;}
.document-body th,.document-body td{border:1px solid var(--rd-hairline);padding:.55rem .7rem;text-align:left;}
.document-body th{background:var(--rd-surface-2);font-weight:600;}
.content-box{border-radius:14px;box-shadow:none;}

/* flip cards — inherit the reader palette + a touch of extra polish */
.flip-card-front{background:var(--rd-surface);border-color:var(--rd-hairline);box-shadow:var(--rd-shadow-card);}
.flip-card-back{
  background:linear-gradient(158deg,var(--rd-accent),color-mix(in srgb,var(--rd-accent) 58%,#000));
  box-shadow:var(--rd-shadow-card);
}
.flip-card-hint{background:color-mix(in srgb,currentColor 14%,transparent);}
.flip-card-nav button{background:var(--rd-surface);border-color:var(--rd-hairline-2);color:var(--rd-ink);}
.flip-card-nav button:hover{background:var(--rd-tint);border-color:var(--rd-accent);color:var(--rd-accent);}
.flip-card-nav .card-counter{color:var(--rd-ink-2);}

/* ===== reader toolbar ===== */
.rd-toolbar{
  position:fixed;top:0;left:0;right:0;height:52px;z-index:900;
  display:flex;align-items:center;gap:4px;padding:0 12px;
  background:var(--rd-material);
  -webkit-backdrop-filter:saturate(180%) blur(20px);backdrop-filter:saturate(180%) blur(20px);
  border-bottom:1px solid var(--rd-hairline);
}
@supports not ((-webkit-backdrop-filter:blur(1px)) or (backdrop-filter:blur(1px))){.rd-toolbar{background:var(--rd-bg);}}
.rd-tb-btn{
  height:34px;min-width:34px;padding:0 8px;border:0;border-radius:9px;background:transparent;
  color:var(--rd-ink);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:6px;
  font:600 13px/1 var(--rd-font);letter-spacing:-.01em;transition:background .12s;
}
.rd-tb-btn:hover{background:var(--rd-fill);}
.rd-tb-btn[aria-pressed="true"],.rd-tb-btn[aria-expanded="true"]{background:var(--rd-tint);color:var(--rd-accent);}
.rd-tb-btn svg{width:19px;height:19px;}
.rd-tb-btn:focus-visible{outline:none;box-shadow:0 0 0 2px var(--rd-bg),0 0 0 4px var(--rd-accent);}
.rd-tb-title{font:600 13px/1.2 var(--rd-font);letter-spacing:-.01em;color:var(--rd-ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:40vw;padding:0 4px;}
.rd-tb-spacer{flex:1;}
.rd-tb-grp{position:relative;}
.rd-tb-contents{display:none;}
.rd-progress-top{position:fixed;top:52px;left:0;right:0;height:2px;z-index:899;pointer-events:none;}
.rd-progress-top>span{display:block;height:100%;width:0;background:var(--rd-accent);transition:width .18s ease;}

.rd-pop{
  position:absolute;top:calc(100% + 8px);right:0;min-width:250px;padding:10px;border-radius:14px;
  background:var(--rd-surface);border:1px solid var(--rd-hairline-2);box-shadow:var(--rd-shadow-pop);
  opacity:0;transform:translateY(-6px) scale(.98);transform-origin:top right;pointer-events:none;
  transition:opacity .15s,transform .16s cubic-bezier(.22,1,.36,1);
}
.rd-pop.open{opacity:1;transform:none;pointer-events:auto;}
.rd-pop-row{display:flex;align-items:center;gap:8px;padding:8px 4px;}
.rd-pop-row+.rd-pop-row{border-top:1px solid var(--rd-hairline);}
.rd-pop-label{font:600 11px/1 var(--rd-font);text-transform:uppercase;letter-spacing:.05em;color:var(--rd-ink-3);flex:1;}
.rd-seg{display:inline-flex;padding:2px;background:var(--rd-fill);border-radius:9px;gap:2px;}
.rd-seg button{border:0;background:transparent;color:var(--rd-ink-2);cursor:pointer;padding:6px 10px;border-radius:7px;font:600 12px/1 var(--rd-font);}
.rd-seg button[aria-checked="true"]{background:var(--rd-surface);color:var(--rd-ink);box-shadow:0 1px 2px rgba(0,0,0,.16);}
html[data-rd-theme="dark"] .rd-seg button[aria-checked="true"]{background:var(--rd-fill-2);}
.rd-step{display:inline-flex;align-items:center;gap:3px;}
.rd-step button{width:30px;height:30px;border:1px solid var(--rd-hairline-2);background:var(--rd-surface);color:var(--rd-ink);border-radius:8px;cursor:pointer;font:600 13px/1 var(--rd-font);display:inline-flex;align-items:center;justify-content:center;}
.rd-step button:hover{background:var(--rd-fill);}
.rd-step .rd-step-val{min-width:44px;text-align:center;font:600 12px/1 var(--rd-font);color:var(--rd-ink-2);}

/* ===== contents rail ===== */
.toc-sidebar{
  width:300px;min-width:300px;background:var(--rd-sidebar-bg);
  border-right:1px solid var(--rd-hairline);padding:1.1rem .9rem 3rem;
  top:52px;height:calc(100vh - 52px);
}
.toc-sidebar nav{position:relative;}
.rd-toc-head{padding:.2rem .4rem .7rem;}
.toc-title{font-family:var(--rd-font);font-weight:700;letter-spacing:-.02em;font-size:1.05rem;color:var(--rd-ink);border:0;padding:0;margin:0 0 .55rem;}
.rd-toc-progress{display:flex;align-items:center;gap:8px;font:600 11px/1 var(--rd-font);color:var(--rd-ink-3);margin-bottom:.6rem;}
.rd-toc-progress .bar{flex:1;height:4px;border-radius:2px;background:var(--rd-fill-2);overflow:hidden;}
.rd-toc-progress .bar>span{display:block;height:100%;width:0;background:var(--rd-done);transition:width .3s;}
.rd-toc-search{width:100%;padding:7px 10px;border-radius:9px;border:1px solid var(--rd-hairline-2);background:var(--rd-surface);color:var(--rd-ink);font:500 12.5px/1 var(--rd-font);}
.rd-toc-search:focus{outline:none;border-color:var(--rd-accent);box-shadow:0 0 0 3px var(--rd-tint);}
.toc-list{position:relative;margin:0;padding:0;list-style:none;}
.toc-list::before{content:"";position:absolute;left:12px;top:10px;bottom:10px;width:2px;background:var(--rd-hairline-2);}
.toc-item{margin:0;position:relative;}
.toc-item a{display:block;padding:7px 8px 7px 30px;border-radius:8px;color:var(--rd-ink-2);font-size:.84rem;line-height:1.35;text-decoration:none;position:relative;transition:background .12s,color .12s;}
.toc-item a::before{content:"";position:absolute;left:7px;top:50%;transform:translateY(-50%);width:11px;height:11px;border-radius:50%;background:var(--rd-sidebar-bg);border:2px solid var(--rd-node);transition:background .15s,border-color .15s;}
.toc-item a:hover{background:var(--rd-fill);color:var(--rd-ink);}
.toc-item.is-done a::before{background:var(--rd-done);border-color:var(--rd-done);}
.toc-item.is-done a::after{content:"";position:absolute;left:11px;top:calc(50% - 1px);width:3px;height:6px;border:solid #fff;border-width:0 2px 2px 0;transform:translateY(-50%) rotate(45deg);}
.toc-item.is-current a{background:var(--rd-tint);color:var(--rd-accent);font-weight:650;}
.toc-item.is-current a::before{background:var(--rd-accent);border-color:var(--rd-accent);box-shadow:0 0 0 4px var(--rd-tint);}
.toc-item.toc-level-3 a{padding-left:42px;font-size:.8rem;}
.toc-item.toc-level-3 a::before{left:19px;width:8px;height:8px;}
.toc-item[hidden]{display:none;}
.toc-empty{color:var(--rd-ink-3);padding:.5rem;}

/* ===== section media chips ===== */
.section-media{display:flex;flex-wrap:wrap;gap:6px;margin:.1rem 0 1.1rem;}
.media-icon{display:none;align-items:center;justify-content:flex-start;gap:6px;width:auto;height:30px;padding:0 12px 0 10px;border-radius:999px;border:1px solid var(--rd-hairline-2);background:var(--rd-surface);color:var(--rd-ink-2);cursor:pointer;font:600 12px/1 var(--rd-font);transition:background .12s,border-color .12s,color .12s;}
.media-icon svg{width:15px;height:15px;flex:none;}
.media-icon.has-content{display:inline-flex;color:var(--rd-ink);}
.media-icon.has-content:hover{background:var(--rd-tint);border-color:var(--rd-accent);color:var(--rd-accent);}
.media-icon.has-content::after{display:none;}
.rd-mi-label{white-space:nowrap;}

/* ===== notes / bookmark restyle (override the runtime's injected styles) ===== */
.note-btn{right:4px!important;top:4px!important;width:26px!important;height:26px!important;background:var(--rd-fill)!important;color:var(--rd-ink-2)!important;box-shadow:none!important;font-size:12px!important;opacity:0!important;}
.rd-card:hover .note-btn,.note-btn.has-note,.note-btn:focus-visible{opacity:1!important;}
.note-btn.has-note{background:var(--rd-tint)!important;color:var(--rd-accent)!important;}
body.rd-notes-off .note-btn,body.rd-notes-off .note-panel{display:none!important;}
.note-panel{right:0!important;left:auto!important;top:calc(100% + 6px)!important;width:min(280px,72vw)!important;background:var(--rd-surface)!important;border:1px solid var(--rd-hairline-2)!important;border-radius:12px!important;box-shadow:var(--rd-shadow-pop)!important;color:var(--rd-ink)!important;}
.note-panel textarea{background:var(--rd-bg)!important;color:var(--rd-ink)!important;border:1px solid var(--rd-hairline-2)!important;}
.note-panel-actions button{background:var(--rd-surface)!important;color:var(--rd-ink)!important;border:1px solid var(--rd-hairline-2)!important;border-radius:8px!important;}
.bm-bar{display:none!important;}
.bm-toast{left:50%!important;right:auto!important;bottom:2rem!important;transform:translateX(-50%)!important;background:var(--rd-ink)!important;color:var(--rd-bg)!important;border-radius:11px!important;font-weight:600!important;}

.back-to-top{width:42px;height:42px;border-radius:50%;background:var(--rd-material);-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px);border:1px solid var(--rd-hairline-2);color:var(--rd-ink);box-shadow:var(--rd-shadow-card);}

.rd-scrim{position:fixed;inset:0;z-index:899;background:rgba(0,0,0,.4);opacity:0;pointer-events:none;transition:opacity .2s;}
@media (max-width:900px){
  .rd-tb-contents{display:inline-flex;}
  .document-wrapper{display:block;}
  .toc-sidebar{position:fixed;top:52px;left:0;z-index:900;height:calc(100vh - 52px);width:84vw;max-width:320px;min-width:0;overflow-y:auto;transform:translateX(-102%);transition:transform .26s cubic-bezier(.22,1,.36,1);box-shadow:var(--rd-shadow-pop);border-right:1px solid var(--rd-hairline);border-bottom:0;}
  body.rd-drawer .toc-sidebar{transform:none;}
  body.rd-drawer .rd-scrim{opacity:1;pointer-events:auto;}
  .rd-tb-title{max-width:44vw;}
  .content{padding-left:1rem;padding-right:1rem;}
  .rd-card{border-radius:14px;padding:1.3rem 1.15rem;}
  .rd-pop{position:fixed;top:56px;right:8px;left:8px;min-width:0;}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important;scroll-behavior:auto!important;}}
@media print{
  .rd-toolbar,.rd-progress-top,.toc-sidebar,.back-to-top,.note-btn,.section-media,.rd-scrim{display:none!important;}
  .document-wrapper{padding-top:0;}
  .rd-card{border:0;box-shadow:none;padding:0;margin:0 0 1rem;break-inside:avoid;}
  .content{max-width:none;}
}
'''

READER_TOOLBAR_HTML = '''<div class="rd-toolbar" role="toolbar" aria-label="Reader controls">
  <button class="rd-tb-btn rd-tb-contents" id="rdContentsBtn" aria-label="Contents" aria-expanded="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h11"/></svg></button>
  <span class="rd-tb-title" id="rdTbTitle"></span>
  <span class="rd-tb-spacer"></span>
  <div class="rd-tb-grp">
    <button class="rd-tb-btn" id="rdAaBtn" aria-label="Text size and appearance" aria-expanded="false" style="font-family:Georgia,serif;"><span style="font-size:16px;">A</span><span style="font-size:11px;">a</span></button>
    <div class="rd-pop" id="rdAaPop" role="dialog" aria-label="Appearance">
      <div class="rd-pop-row"><span class="rd-pop-label">Text size</span><span class="rd-step"><button type="button" id="rdSizeDown" aria-label="Smaller text">A&#8722;</button><span class="rd-step-val" id="rdSizeVal">100%</span><button type="button" id="rdSizeUp" aria-label="Larger text">A+</button></span></div>
      <div class="rd-pop-row"><span class="rd-pop-label">Font</span><span class="rd-seg" id="rdFontSeg" role="radiogroup" aria-label="Font"><button type="button" role="radio" data-font="sans" aria-checked="true">Sans</button><button type="button" role="radio" data-font="serif" aria-checked="false">Serif</button></span></div>
      <div class="rd-pop-row"><span class="rd-pop-label">Theme</span><span class="rd-seg" id="rdThemeSeg" role="radiogroup" aria-label="Theme"><button type="button" role="radio" data-theme="light" aria-checked="false">Light</button><button type="button" role="radio" data-theme="dark" aria-checked="false">Dark</button><button type="button" role="radio" data-theme="auto" aria-checked="true">Auto</button></span></div>
    </div>
  </div>
  <button class="rd-tb-btn" id="rdNotesBtn" aria-label="Toggle notes" aria-pressed="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6a2 2 0 0 1 2-2h8l6 6v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M14 4v6h6"/><path d="M8.5 13.5h5M8.5 16.5h3"/></svg></button>
  <button class="rd-tb-btn" id="rdBookmarkBtn" aria-label="Bookmark this position" aria-pressed="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg></button>
</div>
<div class="rd-progress-top" aria-hidden="true"><span id="rdProgressTop"></span></div>
<div class="rd-scrim" id="rdScrim"></div>'''

READER_SHELL_SCRIPT = r'''<script>
(function(){
  "use strict";
  var root=document.documentElement, body=document.body;
  function $(id){return document.getElementById(id);}
  function getLS(k,d){try{var v=localStorage.getItem(k);return v==null?d:v;}catch(e){return d;}}
  function setLS(k,v){try{localStorage.setItem(k,v);}catch(e){}}
  function syncSeg(id,attr,val){var s=$(id);if(!s)return;[].forEach.call(s.querySelectorAll('button'),function(b){b.setAttribute('aria-checked',String(b.getAttribute(attr)===String(val)));});}
  function wireSeg(id,attr,fn){var s=$(id);if(!s)return;[].forEach.call(s.querySelectorAll('button'),function(b){b.addEventListener('click',function(){fn(b.getAttribute(attr));});});}

  /* ---- theme ---- */
  var mq=window.matchMedia('(prefers-color-scheme: dark)');
  function applyTheme(mode){
    var dark=mode==='dark'||(mode!=='light'&&mq.matches);
    root.setAttribute('data-rd-theme',dark?'dark':'light');
    setLS('rd-theme',mode); syncSeg('rdThemeSeg','data-theme',mode);
  }
  try{mq.addEventListener('change',function(){if(getLS('rd-theme','auto')==='auto')applyTheme('auto');});}catch(e){}
  wireSeg('rdThemeSeg','data-theme',applyTheme);
  syncSeg('rdThemeSeg','data-theme',getLS('rd-theme','auto'));

  /* ---- text size + font ---- */
  var scale=parseFloat(getLS('rd-scale','1'))||1;
  function applyScale(s){
    s=Math.min(1.5,Math.max(0.85,Math.round(s*100)/100));
    root.style.setProperty('--rd-scale',s);
    var v=$('rdSizeVal'); if(v)v.textContent=Math.round(s*100)+'%';
    setLS('rd-scale',String(s)); return s;
  }
  function applyFont(f){
    var d=document.querySelector('.document-body');
    if(d){ if(f==='serif')d.setAttribute('data-rd-serif','');else d.removeAttribute('data-rd-serif'); }
    setLS('rd-font',f); syncSeg('rdFontSeg','data-font',f);
  }
  scale=applyScale(scale); applyFont(getLS('rd-font','sans'));
  if($('rdSizeDown'))$('rdSizeDown').addEventListener('click',function(){scale=applyScale(scale-0.05);});
  if($('rdSizeUp'))$('rdSizeUp').addEventListener('click',function(){scale=applyScale(scale+0.05);});
  wireSeg('rdFontSeg','data-font',applyFont);

  /* ---- appearance popover ---- */
  var aaBtn=$('rdAaBtn'), aaPop=$('rdAaPop');
  if(aaBtn&&aaPop){
    function closeAa(){aaPop.classList.remove('open');aaBtn.setAttribute('aria-expanded','false');}
    aaBtn.addEventListener('click',function(e){e.stopPropagation();var o=aaPop.classList.toggle('open');aaBtn.setAttribute('aria-expanded',String(o));});
    document.addEventListener('click',function(e){if(!e.target.closest('#rdAaPop')&&!e.target.closest('#rdAaBtn'))closeAa();});
    document.addEventListener('keydown',function(e){if(e.key==='Escape')closeAa();});
  }

  /* ---- wrap sections into cards ---- */
  var db=document.querySelector('.document-body');
  if(db){
    try{
      var kids=[].slice.call(db.childNodes), groups=[], cur=null;
      kids.forEach(function(n){
        if((n.nodeType===1&&/^H[12]$/.test(n.tagName))||!cur){cur=[];groups.push(cur);}
        cur.push(n);
      });
      groups.forEach(function(g){
        var real=g.some(function(n){return n.nodeType===1||(n.nodeType===3&&n.nodeValue.trim());});
        if(!real)return;
        var card=document.createElement('section'); card.className='rd-card';
        g.forEach(function(n){card.appendChild(n);});
        db.appendChild(card);
      });
    }catch(e){}
  }

  /* ---- contents rail: progress + search ---- */
  var nav=document.querySelector('.toc-sidebar nav');
  var tocItems=[].slice.call(document.querySelectorAll('.toc-item'));
  if(nav){
    var head=document.createElement('div'); head.className='rd-toc-head';
    var title=nav.querySelector('.toc-title'); if(title)head.appendChild(title);
    head.insertAdjacentHTML('beforeend',
      '<div class="rd-toc-progress"><span class="bar"><span id="rdTocBar"></span></span><span id="rdTocPct">0%</span></div>'+
      '<input type="search" class="rd-toc-search" id="rdTocSearch" placeholder="Filter sections" autocomplete="off" />');
    nav.insertBefore(head,nav.firstChild);
    var si=$('rdTocSearch');
    if(si)si.addEventListener('input',function(){
      var q=si.value.trim().toLowerCase();
      tocItems.forEach(function(li){li.hidden=!!q&&li.textContent.toLowerCase().indexOf(q)<0;});
    });
  }

  /* ---- reading progress + scroll spy ---- */
  var headings=[].slice.call(document.querySelectorAll('.document-body h1[id],.document-body h2[id],.document-body h3[id]'));
  var tocById={};
  tocItems.forEach(function(li){var a=li.querySelector('a[href^="#"]');if(a){try{tocById[decodeURIComponent(a.getAttribute('href').slice(1))]=li;}catch(e){}}});
  var PKEY='rd-progress-'+(location.pathname||'x').replace(/[^a-z0-9]/gi,'').slice(-40);
  var furthest=parseInt(getLS(PKEY,'0'),10)||0;
  function cleanText(h){var c=h.cloneNode(true);[].forEach.call(c.querySelectorAll('button,.note-panel'),function(b){b.remove();});return c.textContent.trim();}
  function curIdx(){
    var y=window.scrollY+130, idx=0;
    for(var i=0;i<headings.length;i++){
      if(headings[i].getBoundingClientRect().top+window.scrollY<=y)idx=i; else break;
    }
    return idx;
  }
  function refresh(){
    var idx=curIdx();
    if(idx>furthest){furthest=idx;setLS(PKEY,String(furthest));}
    headings.forEach(function(h,i){
      var li=tocById[h.id]; if(!li)return;
      li.classList.toggle('is-done', i<=furthest&&i!==idx);
      li.classList.toggle('is-current', i===idx);
    });
    var total=headings.length||1;
    var hpct=Math.min(100,Math.round(((idx+1)/total)*100));
    var tb=$('rdTocBar'); if(tb)tb.style.width=hpct+'%';
    var tp=$('rdTocPct'); if(tp)tp.textContent=hpct+'%';
    var sp=document.documentElement.scrollHeight-window.innerHeight;
    var spct=sp>0?Math.round(window.scrollY/sp*100):0;
    var pt=$('rdProgressTop'); if(pt)pt.style.width=Math.min(100,spct)+'%';
    var tt=$('rdTbTitle'); if(tt&&headings[idx])tt.textContent=cleanText(headings[idx]);
  }
  var raf;
  window.addEventListener('scroll',function(){if(raf)return;raf=requestAnimationFrame(function(){raf=0;refresh();});},{passive:true});
  window.addEventListener('resize',function(){refresh();},{passive:true});
  refresh();

  /* ---- contents drawer (mobile) ---- */
  var cbtn=$('rdContentsBtn'), scrim=$('rdScrim');
  function drawer(open){body.classList.toggle('rd-drawer',open);if(cbtn)cbtn.setAttribute('aria-expanded',String(open));}
  if(cbtn)cbtn.addEventListener('click',function(){drawer(!body.classList.contains('rd-drawer'));});
  if(scrim)scrim.addEventListener('click',function(){drawer(false);});
  document.querySelectorAll('.toc-item a').forEach(function(a){a.addEventListener('click',function(){if(window.innerWidth<=900)drawer(false);});});

  /* ---- media chips: add labels, keep the runtime's click handlers ---- */
  var MI={video:'Video',audio:'Audio',pptx:'Slides',h5p:'Activity',url:'Link',glossary:'Glossary'};
  document.querySelectorAll('.media-icon.has-content').forEach(function(b){
    var t=b.getAttribute('data-media-type');
    if(MI[t]&&!b.querySelector('.rd-mi-label')){
      var s=document.createElement('span');s.className='rd-mi-label';s.textContent=MI[t];b.appendChild(s);
    }
  });

  /* ---- media popups: delegated fallback ----
     The publish runtime binds a per-button click handler that calls
     stopPropagation(); when that fires this never runs. But section cards /
     other DOM work can leave those bindings ineffective, so this catches
     any media click that bubbles up to the document and opens the popup
     itself. Kept deliberately close to the runtime's own markup/behaviour. */
  (function(){
    function embed(u){
      var m;
      if((m=u.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/shorts\/|youtube\.com\/live\/)([A-Za-z0-9_-]{11})/)))return'https://www.youtube.com/embed/'+m[1];
      if((m=u.match(/youtube\.com\/.*[?&]v=([A-Za-z0-9_-]{11})/)))return'https://www.youtube.com/embed/'+m[1];
      if((m=u.match(/vimeo\.com\/(\d+)/)))return'https://player.vimeo.com/video/'+m[1];
      if((m=u.match(/drive\.google\.com\/file\/d\/([^/]+)/)))return'https://drive.google.com/file/d/'+m[1]+'/preview';
      if((m=u.match(/loom\.com\/share\/([A-Za-z0-9]+)/)))return'https://www.loom.com/embed/'+m[1];
      return null;
    }
    function esc(s){return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');}
    function ensurePopup(){
      var p=document.getElementById('mediaPopup');
      if(p)return p;
      p=document.createElement('div');
      p.className='media-popup-overlay';p.id='mediaPopup';
      p.setAttribute('role','dialog');p.setAttribute('aria-modal','true');
      p.innerHTML='<div class="media-popup"><div class="media-popup-header"><h3 id="mediaPopupTitle"></h3>'+
        '<button type="button" class="media-popup-close" id="mediaPopupClose" aria-label="Close">&times;</button></div>'+
        '<div class="media-popup-body" id="mediaPopupBody"></div></div>';
      document.body.appendChild(p);
      return p;
    }
    function build(type,src){
      switch(type){
        case'video':
          return '<video class="video-js vjs-big-play-centered vjs-fluid" controls preload="auto" style="width:100%"><source src="'+esc(src)+'" type="video/mp4"/></video>';
        case'audio':
          return '<audio controls autoplay src="'+esc(src)+'" style="width:100%">Audio not supported.</audio>';
        case'pptx':
          if(/\.pdf($|\?)/i.test(src))return '<iframe src="'+esc(src)+'" style="width:100%;min-height:70vh;border:none"></iframe>';
          return '<div style="text-align:center;padding:2rem"><a href="'+esc(src)+'" download style="padding:.7rem 1.4rem;background:var(--rd-accent);color:#fff;border-radius:10px;text-decoration:none">Download presentation</a></div>';
        case'h5p':
          if(/^https?:\/\//.test(src))return '<iframe src="'+esc(src)+'" style="width:100%;min-height:60vh;border:none;border-radius:8px" allowfullscreen></iframe>';
          return '<iframe src="'+esc(src)+'" style="width:100%;min-height:60vh;border:none" allowfullscreen></iframe>';
        case'glossary':
          var parts=src.split('|');
          return '<div style="font-size:1.15rem;font-weight:700;margin-bottom:.5rem">'+esc((parts[0]||'').trim())+'</div><div style="line-height:1.6">'+esc((parts[1]||src).trim())+'</div>';
        case'url':
          var e=embed(src);
          if(e)return '<iframe src="'+esc(e)+'" allowfullscreen style="width:100%;min-height:60vh;border:none;border-radius:8px"></iframe>';
          return '<div style="text-align:center;padding:2rem"><a href="'+esc(src)+'" target="_blank" rel="noopener" style="padding:.7rem 1.4rem;background:var(--rd-accent);color:#fff;border-radius:10px;text-decoration:none">Open in new tab</a></div>';
        default:
          return '<a href="'+esc(src)+'" target="_blank" rel="noopener">Open file</a>';
      }
    }
    function openMedia(type,src){
      var pop=ensurePopup();
      var body=pop.querySelector('#mediaPopupBody');
      var head=pop.querySelector('#mediaPopupTitle');
      if(head)head.textContent=(MI[type]||type.charAt(0).toUpperCase()+type.slice(1));
      body.innerHTML=build(type,src);
      pop.classList.add('visible');
      var player=null,vid=body.querySelector('.video-js');
      if(vid&&window.videojs){try{vid.id=vid.id||('vp-'+Date.now());player=window.videojs(vid);}catch(e){}}
      function close(){pop.classList.remove('visible');if(player){try{player.dispose();}catch(e){}player=null;}body.innerHTML='';}
      var x=pop.querySelector('#mediaPopupClose');if(x)x.onclick=close;
      pop.addEventListener('click',function h(ev){if(ev.target===pop){close();pop.removeEventListener('click',h);}});
      document.addEventListener('keydown',function k(ev){if(ev.key==='Escape'){close();document.removeEventListener('keydown',k);}});
    }
    document.addEventListener('click',function(e){
      var t=e.target;
      var btn=t&&t.closest?t.closest('.media-icon.has-content'):null;
      if(!btn)return;
      var pop=document.getElementById('mediaPopup');
      if(pop&&pop.classList.contains('visible'))return; // runtime already handled it
      var src=btn.getAttribute('data-media-src');
      if(!src)return;
      e.preventDefault();
      openMedia(btn.getAttribute('data-media-type')||'url',src);
    });
  })();

  /* ---- notes toggle ---- */
  var nBtn=$('rdNotesBtn');
  if(nBtn)nBtn.addEventListener('click',function(){
    var off=body.classList.toggle('rd-notes-off');
    nBtn.setAttribute('aria-pressed',String(!off));
  });

  /* ---- flip cards: keyboard flip ---- */
  document.addEventListener('keydown',function(e){
    if(e.key!=='Enter'&&e.key!==' ')return;
    var c=e.target&&e.target.closest?e.target.closest('.flip-card'):null;
    if(c){e.preventDefault();c.classList.toggle('flipped');}
  });

  /* ---- bookmark (proxy the runtime's hidden save button) ---- */
  var bmBtn=$('rdBookmarkBtn');
  if(bmBtn)bmBtn.addEventListener('click',function(){
    var s=document.querySelector('.bm-save');
    if(s){s.click();bmBtn.setAttribute('aria-pressed','true');setTimeout(function(){bmBtn.setAttribute('aria-pressed','false');},1600);}
  });
})();
</script>'''


def publish_document(job_dir, filename):
    """
    Main publish orchestrator.

    Uploads all assets for a document to OCI Object Storage:
    - Media files -> poc-interactivetxt-media-src-bucket
    - Video files -> poc-interactivetxt-media-dst-bucket
    - Rewritten HTML -> poc-interactivetxtbk1

    Args:
        job_dir: The job directory name (e.g., "abc123_document")
        filename: The HTML filename (e.g., "document.html")

    Returns:
        dict with keys: html_url, media_uploaded, videos_uploaded
    """
    output_path = Path(OUTPUT_DIR) / job_dir
    html_path = output_path / filename

    if not html_path.exists():
        raise FileNotFoundError(f"Document not found: {html_path}")

    client, namespace = _get_oci_client()
    base_key = job_dir

    # Collect files to upload
    media_map = {}  # relative_path -> public_url
    video_map = {}  # relative_path -> public_url

    for root, _dirs, files in os.walk(str(output_path)):
        for fname in files:
            full_path = os.path.join(root, fname)
            relative = os.path.relpath(full_path, str(output_path))

            # Skip the HTML file itself and non-media files
            if relative == filename or _is_skip_file(full_path):
                continue

            object_name = f"{base_key}/{relative}"

            if _is_video_file(full_path):
                # Videos must be publicly readable so the learner's browser can
                # load them. VIDEO_BUCKET (poc-interactivetxt-media-dst-bucket)
                # is private (NoPublicAccess), so a plain object URL to it 404s
                # for learners ("media could not be loaded"). Publish videos to
                # the public media bucket (ObjectRead), matching images, so the
                # embedded URL actually resolves. We still upload a copy to the
                # video bucket for any downstream streaming/transcode pipeline.
                _upload_file(client, namespace, VIDEO_BUCKET, full_path, object_name)
                _upload_file(client, namespace, MEDIA_BUCKET, full_path, object_name)
                video_map[relative] = _get_public_url(namespace, MEDIA_BUCKET, object_name)
            else:
                _upload_file(client, namespace, MEDIA_BUCKET, full_path, object_name)
                media_map[relative] = _get_public_url(namespace, MEDIA_BUCKET, object_name)

    # Rewrite HTML paths to OCI public URLs
    html_content = html_path.read_text(encoding="utf-8")

    # Strip editor UI (buttons, toolbar, modals, editing scripts) for learner view
    html_content = _strip_editor_ui(html_content)

    # Replace relative paths with public URLs (media + video)
    all_mappings = {**media_map, **video_map}
    for relative_path, public_url in all_mappings.items():
        # Handle both ./path and path references
        escaped = re.escape(relative_path)
        pattern = rf'(?:\.\/)?{escaped}'
        html_content = re.sub(pattern, public_url, html_content)

    # Upload the rewritten HTML
    html_object_name = f"{base_key}/{filename}"
    _upload_bytes(client, namespace, HTML_BUCKET, html_content.encode("utf-8"), html_object_name)
    html_url = _get_public_url(namespace, HTML_BUCKET, html_object_name)

    _log.info(
        f"Published {job_dir}/{filename}: "
        f"{len(media_map)} media, {len(video_map)} videos"
    )

    return {
        "html_url": html_url,
        "media_uploaded": len(media_map),
        "videos_uploaded": len(video_map),
    }

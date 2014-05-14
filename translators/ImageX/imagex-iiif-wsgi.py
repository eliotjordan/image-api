# IIIF shim for ERDAS Image Web Server ImageX protocol

# Adapted from work by Rob Sanderson (azaroth42@gmail.com)
# Adapted by eliotjordan

import cgitb
from wsgiref import headers
import urllib
import StringIO
import re
import logging
import lxml.etree as ET

# Make a global logging object
LOG_LEVEL = logging.DEBUG

logger = logging.getLogger("iiiflog")
logger.setLevel(LOG_LEVEL)
logStreamHandler = logging.StreamHandler()
logFormatter = logging.Formatter("%(levelname)s %(asctime)s %(funcName)s %(lineno)d %(message)s")
logStreamHandler.setFormatter(logFormatter)
logger.addHandler(logStreamHandler)

try:
    import json
except:
    import simplejson as json

# ----- CONFIGURATION OPTIONS -----

# Base URL of this IIIF service
BASEURL = "http://appserver"
# Prefix for this IIIF service
PREFIX = "/iiif"
# Arbitrary tile size
TILE_SIZE = 256
# Arbitrary scale_factors
# Currently: 100%, 50%, 25%, 12.5%
SCALE_FACTORS = [1,2,4,8]

# URL of ERDAS Image Web Server and service root
IMX_BASE = "http://imageserver"
IMX_ROOT = "/erdas-iws/erdas/imagex/prmap"

# Max image size returned by ImageX Server
MAX_IMAGE_SIZE = 10000

# ---------------------------------


# Just keep cache in memory for now
# INFO_CACHE[imageid] = (int, int, "jpg")
INFO_CACHE = {}

def parse_qs(data):
    # Py 2.4 doesn't have list comprehensions :(
    d = {}
    try:
        bits = data.split('&')
        for b in bits:
            (n,v) = b.split('=')
            d[n]=urllib.unquote_plus(v)
    except:
        pass
    return d

class WsgiApp:

    def __init__(self):
        self.codes = {
            200 : 'OK',
            201 : 'Created',
            202 : 'Accepted',
            300 : 'Multiple Choies',
            301 : 'Moved Permanently',
            302 : 'Found',
            303 : 'See Other',
            304 : 'Not Modified',
            400 : 'Bad Request',
            401 : 'Unauthorized',
            403 : 'Forbidden',
            404 : 'Not Found',
            405 : 'Method Not Allowed',
            406 : 'Not Acceptable',
            415 : 'Invalid Media',
            500 : 'Internal Server Error',
            501 : 'Not Implemented',
            503 : 'Service Unavailable'
        }

    def __call__(self, environ, start_response):
        self.environ = environ
        self.start_response = start_response

        try:
            self.full_uri = environ.get('SCRIPT_URI')
            self.path = environ['SCRIPT_URL'][len(environ['SCRIPT_NAME']):]
            self.host = environ['wsgi.url_scheme'] + "://" + environ['SERVER_NAME']
        except:
            self.full_uri = environ.get('REQUEST_URI')
            self.path = environ['PATH_INFO']
            self.host = environ.get('HTTP_HOST')

        if environ['QUERY_STRING']:
            self.query = parse_qs(environ['QUERY_STRING'])

        else:
            self.query = {}

        try:
            self.body = environ.get['wsgi.input'].read()
        except:
            self.body = ''

        h = {}
        for (k,v) in environ.items():
            if k.startswith('HTTP_'):
                name = k[5:].lower().replace('_', '-')
                h[name] = v
        self.in_headers = h

        self.status = 200
        self.out_headers = headers.Headers([])

        try:
            data = self.handle()
        except:
            sio = StringIO.StringIO()
            cgitb.Hook(file=sio).handle()
            sio.seek(0)
            data = sio.read()
            self.out_headers['Content-type'] = 'text/html'
            self.status = 500
            
        self.out_headers['Content-length'] = str(len(data))    
        status = "%s %s" % (self.status, self.codes[self.status])
        start_response(status, self.out_headers.items())

        if type(data) == str:
            return [data]
        elif type(data) == unicode:
            return [data.encode('utf-8')]
        else:
            # see if response is iterable
            try:
                iter(data)
                return data
            except TypeError:
                return [data]

    def send(self, data, status=200, ct = 'text/html'):

        self.status = status
        self.out_headers['Content-type'] = ct
        return data


class ServiceHandler(WsgiApp):

    def __init__(self):
        WsgiApp.__init__(self)
        self.identifiers = {}

        fns = []
        self.extensions = {
            'jpg': 'image/jpeg',
            'png': 'image/png'
        }

        self.compliance = "http://library.stanford.edu/iiif/image-api/1.1/compliance.html#level1"
        
        id = "([^#?@]+)"
        region = "(full|(pct:)?([\d.]+,){3}([\d.]+))"
        size = "(full|[\d.]+,|,[\d.]+|pct:[\d.]+|[\d.]+,[\d.]+|![\d.]+,[\d.]+)"
        rot = "([0-9.+])"
        quality = "(native|color|grey|bitonal)"
        format = "(jpg|tif|png|gif|jp2|pdf|eps|bmp)"        
        self.idRe = re.compile(id)
        self.regionRe = re.compile(region)
        self.sizeRe = re.compile(size)
        self.rotationRe = re.compile(rot)
        self.qualityRe = re.compile(quality)
        self.formatRe = re.compile(format)        
        self.infoRe = re.compile("/" + id + '/info.(xml|json)')
        self.badcharRe= re.compile('[\[\]?@#]')
                        
    def error_msg(self, param, msg, status):
        text = "An error occured when processing the '%s' parameter:  %s" % (param, msg)
        self.status = status
        self.out_headers['Content-type'] = 'text/plain'
        return text

    def make_info(self, infoId):

        try:
            (imageW, imageH) = INFO_CACHE[infoId]
        except:

            infoURI =  IMX_BASE + IMX_ROOT + ("?request=dsinfo&layers=%s" % (infoId))

            u = urllib.urlopen(infoURI)
            data = u.read()

            if u.code == 200:
                elem = ET.fromstring(data)

                imageW = elem.get("width")
                imageH =    elem.get("height")
                
                if imageW is None or imageH is None:
                    return None
                
                INFO_CACHE[infoId] = (imageW, imageH)
                
            else:
                return None

        qualities = ['native','color']

        formats = ['jpg','png']

        info = {"@id": "%s%s/%s" % (BASEURL, PREFIX, infoId),
                "@context" : "http://library.stanford.edu/iiif/image-api/1.1/context.json",
                "width": int(imageW),
                "height": int(imageH),
                "tile_width": TILE_SIZE,
                "tile_height": TILE_SIZE,
                "scale_factors": SCALE_FACTORS,
                "formats": formats,
                "qualities": qualities,
                "profile": self.compliance}
      
        return info
        
    def handle(self):
        # http://{server}{/prefix}   /{identifier}/{region}/{size}/{rotation}/{quality}{.format}

        # Remove prefix from path
        self.path = self.path.replace(PREFIX,'');

        # first character is / so skip first empty bit
        bits = self.path.split('/')[1:]

        #Start from the end of the path

        #Quality
        if bits:
            quality = bits.pop()
            dotidx = quality.rfind('.')
            if dotidx > -1:
                format = quality[dotidx+1:]
                quality = quality[:dotidx]
            else:
                format = "jpg"

            #If extension is json then this is not an image request
            if format == "json":
                imageReq = False
            else:
                imageReq = True
                if self.qualityRe.match(quality) == None:
                    return self.error_msg("quality", "Quality invalid: %r" % quality, status = 400)
                elif self.formatRe.match(format) == None:
                    return self.error_msg("format", "Format invalid: %r" % format, status = 400)
        else:
            return self.error_msg("quality", "Quality unspecified", status=400)        

        #Rotation
        if imageReq:
            if bits:
                rotation = bits.pop()
                if self.rotationRe.match(rotation) == None:
                    return self.error_msg("rotation", "Rotation invalid: %r" % rotation, status = 400)
            else:
                return self.error_msg("rotation", "Rotation unspecified", status=400)

        #Size
        if imageReq:
            if bits:
                size = bits.pop()
                if self.sizeRe.match(size) == None:
                    return self.error_msg("size", "Size invalid: %r" % size, status = 400)
            else:
                return self.error_msg("size", "Size unspecified", status=400)

        #Region
        if imageReq:
            if bits:
                region = bits.pop()
                if self.regionRe.match(region) == None:
                    return self.error_msg("region", "Region invalid: %r" % region, status = 400)
            else:
                return self.error_msg("region", "Region unspecified", status=400)

        #Identifier
        if bits:

            # if identifier has slashes, join the split segments together with / seperator
            # non-split identifier will be returned as is
            infoId = "/".join(bits)

            if self.idRe.match(infoId) == None:
                return self.error_msg("identifier", "Identifier invalid: %r" % infoId, status=400)
            else:
                # Check []?#@ (will never find / )
                if self.badcharRe.match(infoId):
                    return self.error_msg('identifier', 'Unescaped Characters', status=400)                
    
        else:
            return self.error_msg("identifier", "Identifier unspecified", status=400)

        self.out_headers['Link'] = '<http://library.stanford.edu/iiif/image-api/1.1/compliance.html#level2>;rel="profile"'

        # If this in not an image request, then build and return info
        info = self.make_info(infoId)

        if info is None:
            return self.error_msg("information request", "Problem getting information from the server", status=400)
        
        if not imageReq:    
            mt = "application/json"
            js = self.make_info(infoId)
            data = json.dumps(js, sort_keys=True)
            return self.send(data, 200, ct=mt)
        
        imageW = info['width']
        imageH = info['height']
                    
        # Check region
        if region == 'full':
            # full size of image
            x=0;y=0;w=imageW;h=imageH
        else:
            try:
                (x,y,w,h)=region.split(',')
            except:
                return self.error_msg('region', 'unable to parse region: %r' % region, status=400)
            if x.startswith('pct:'):
                x = x[4:]
                # convert pct into px
                try:
                    x = float(x) ; y = float(y) ; w = float(w) ; h = float(h)
                    x = int(x / 100.0 * float(imageW))
                    y = int(y / 100.0 * float(imageH))
                    w = int(w / 100.0 * float(imageW))
                    h = int(h / 100.0 * float(imageH))
                except TypeError as e:
                    return self.error_msg('region', 'unable to parse region: %r' % region, status=400)                     
            else:
                try:
                    x = int(x) ; y = int(y) ; w = int(w) ; h = int(h)
                except:
                    return self.error_msg('region', 'unable to parse region: %r' % region, status=400) 
                            
            if (x > imageW):
                return self.error_msg("region", "X coordinate is outside image", status=400)
            elif (y > imageH):
                return self.error_msg("region", "Y coordinate is outside image", status=400)
            elif w < 1:
                return self.error_msg("region", "Region width is zero", status=400)
            elif h < 1:
                return self.error_msg("region", "Region height is zero", status=400) 
            if x+w > imageW:
                w = imageW-x            
            if y+h > imageH:
                h = imageH-y            

        # Output Size in imagex scale
        if size == 'full':
            sizeW = imageW
            sizeH = imageH
        else:
            try:
                
                if size[-1:] == ',':   # w,
                  
                    # constrain width to w, and calculate appropriate h
                    sizeW = int(size[:-1])
                    scale = float(sizeW)/float(imageW)
                    sizeH = int(float(imageH) * scale)

                elif size[0] == ',':     # ,h

                    # constrain height to h, and calculate appropriate w
                    sizeH = int(size[1:])
                    scale = float(sizeH)/float(imageH)
                    sizeW = int(float(imageW) * scale)

                elif size[0] == '!':     # !w,h
                    # Must fit inside w and h
                    (maxSizeW, maxSizeH) = size[1:].split(',')
                    # calculate both ratios and pick smaller
                    ratioW = float(maxSizeW) / float(imageW)
                    ratioH = float(maxSizeH) / float(imageH)
                    scale = float(min(ratioW, ratioH))
                    sizeW = int(float(imageW) * scale)
                    sizeH = int(float(imageH) * scale)

                elif size.startswith('pct:'):     #pct: n
                    # n percent of size
                    scale = float(size[4:]) / 100.0
                    sizeH = int(float(imageH) * scale)
                    sizeW = int(float(imageW) * scale)
                else:    # w,h    or invalid
                    (sw,sh) = size.split(',')
                    # exactly w and h, deforming aspect
                    sizeW = int(sw)
                    sizeH = int(sh)           
            except TypeError as e:
                return self.error_msg('size', 'Size unparseable: %r' % size, status=400)      
        
        # Process rotation
        try:
            rotation = float(rotation)
        except:
            return self.error_msg('rotation', 'Rotation unparseable: %r' % rotation, status=400)
        if rotation < 0 or rotation > 360:
            return self.error_msg('rotation', 'Rotation must be 0-359.99: %r' % rotation, status=400)            
        rotation = rotation % 360

        if not quality in info['qualities']:
            return self.error_msg('quality', 'Quality not supported for this image: %r' % quality, status=501)            
        elif quality == info['qualities'][1]:
            quality = 'native'

        # Build ImageX URI
        params = {
            "request":"image",
            "cache":"true",
            "transparent":"true",
            "layers":infoId,  #identifier
            "tlx":x, #x
            "tly":y, #y
            "brx": int(x) + int(w),
            "bry":int(y)+ int(h),
            "sizex":sizeW,
            "sizey":sizeH 
        }

        full = IMX_BASE + IMX_ROOT +  "?" + "&".join(["%s=%s" % x for x in params.items()])

        u = urllib.urlopen(full)
        data = u.read()

        return self.send(data, status=200, ct='image/jpeg')

        # Redirect instead...
        #self.out_headers['Location'] = full
        #return self.send("", status=301)

application = ServiceHandler()

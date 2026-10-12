"""Validated, local icon storage and bounded, DNS-pinned HTTP downloads (stdlib)."""
import concurrent.futures
from contextlib import contextmanager
import io
import hashlib
import http.client
import ipaddress
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlsplit, urljoin
import xml.etree.ElementTree as ET
import zlib

MAX_BYTES = 1024 * 1024
MAX_PIXELS = 4096 * 4096
MIME = {'.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg',
        '.webp':'image/webp', '.svg':'image/svg+xml', '.ico':'image/x-icon'}
NAME = re.compile(r'[A-Za-z0-9_-]{1,80}\.(?:png|jpe?g|webp|svg|ico)\Z')
BUILTIN = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\.(?:png|jpe?g|webp|svg|ico)\Z')
SVG_CSP = "sandbox; default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
DNS_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix='icon-dns')
FETCH_SLOTS = threading.BoundedSemaphore(4)


class IconError(ValueError):
    pass


class IconBusy(IconError):
    pass


OPERATION_SLOTS = threading.BoundedSemaphore(2)


@contextmanager
def operation():
    if not OPERATION_SLOTS.acquire(blocking=False):
        raise IconBusy('图标处理繁忙，请稍后重试')
    try:yield
    finally:OPERATION_SLOTS.release()


def dimensions(w, h):
    if not 0 < w <= 4096 or not 0 < h <= 4096 or w*h > MAX_PIXELS:
        raise IconError('图标尺寸过大或无效（最大 4096×4096）')


def validate_png(b):
    if not b.startswith(b'\x89PNG\r\n\x1a\n'):
        raise IconError('PNG 文件格式不正确')
    pos = 8; ihdr = None; image = bytearray(); ended = False
    while pos + 12 <= len(b):
        size = int.from_bytes(b[pos:pos+4], 'big'); tag = b[pos+4:pos+8]
        end = pos+12+size
        if end > len(b) or not re.fullmatch(b'[A-Za-z]{4}', tag):
            raise IconError('PNG 数据不完整')
        data = b[pos+8:pos+8+size]
        if (zlib.crc32(tag+data) & 0xffffffff) != int.from_bytes(b[end-4:end], 'big'):
            raise IconError('PNG 校验失败')
        if ihdr is None:
            if tag != b'IHDR' or size != 13: raise IconError('PNG 缺少有效头部')
            ihdr = struct.unpack('>IIBBBBB', data)
            w,h,depth,color,compression,filtering,interlace = ihdr
            dimensions(w,h)
            allowed = {0:(1,2,4,8,16), 2:(8,16), 3:(1,2,4,8), 4:(8,16), 6:(8,16)}
            if color not in allowed or depth not in allowed[color] or compression or filtering or interlace not in (0,1):
                raise IconError('PNG 编码不受支持')
        elif tag == b'IHDR': raise IconError('PNG 头部重复')
        if tag in (b'acTL', b'fcTL', b'fdAT'): raise IconError('不支持动态 PNG 图标')
        if tag == b'IDAT': image.extend(data)
        if tag == b'IEND':
            if size or end != len(b): raise IconError('PNG 尾部不正确')
            ended = True; break
        pos = end
    if not ended or not image: raise IconError('PNG 缺少图像数据')
    channels = {0:1,2:3,3:1,4:2,6:4}[color]
    # Bound decompression even for highly compressed images and Adam7 scanlines.
    max_decoded = ((w*channels*depth+7)//8 + 8)*h + h*8
    if max_decoded > 16*1024*1024:
        raise IconError('图标解压后超过 16 MiB，请缩小尺寸')
    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(bytes(image), max_decoded+1)
    except zlib.error as e: raise IconError('PNG 压缩数据无效') from e
    if len(raw)>max_decoded or not decompressor.eof or decompressor.unused_data:
        raise IconError('PNG 压缩数据无效或超出尺寸')
    if not interlace:
        row = (w*channels*depth+7)//8+1
        if len(raw) != row*h or any(raw[i] > 4 for i in range(0,len(raw),row)):
            raise IconError('PNG 像素数据无效')


def validate_jpeg(b):
    if not b.startswith(b'\xff\xd8') or not b.endswith(b'\xff\xd9'):
        raise IconError('JPEG 文件不完整')
    pos=2; found=False; scan=False
    while pos < len(b)-2:
        if b[pos] != 255: raise IconError('JPEG 标记无效')
        while pos < len(b) and b[pos] == 255: pos+=1
        if pos >= len(b): break
        tag=b[pos];pos+=1
        if tag in (0xd8,0xd9,0x00): raise IconError('JPEG 标记无效')
        if tag in range(0xd0,0xd8) or tag==0x01: continue
        if pos+2 > len(b): raise IconError('JPEG 段不完整')
        size=int.from_bytes(b[pos:pos+2], 'big')
        if size < 2 or pos+size > len(b): raise IconError('JPEG 段长度无效')
        if tag in (0xc0,0xc1,0xc2):
            if size<8: raise IconError('JPEG 尺寸无效')
            h,w=struct.unpack('>HH',b[pos+3:pos+7]);dimensions(w,h);found=True
        if tag==0xda:
            scan=pos+size<len(b)-2;break
        pos+=size
    if not found or not scan: raise IconError('JPEG 缺少图像段')


def validate_webp(b):
    if len(b)<20 or b[:4]!=b'RIFF' or b[8:12]!=b'WEBP' or int.from_bytes(b[4:8],'little')+8!=len(b):
        raise IconError('WebP 文件结构无效')
    pos=12; found=False
    while pos+8<=len(b):
        tag=b[pos:pos+4];size=int.from_bytes(b[pos+4:pos+8],'little')
        chunk=b[pos+8:pos+8+size]
        if pos+8+size>len(b): raise IconError('WebP 数据不完整')
        if tag in (b'ANIM',b'ANMF'):raise IconError('不支持动态 WebP 图标')
        if tag==b'VP8 ':
            if len(chunk)<10 or chunk[3:6]!=b'\x9d\x01\x2a': raise IconError('WebP VP8 头部无效')
            dimensions(int.from_bytes(chunk[6:8],'little')&0x3fff,int.from_bytes(chunk[8:10],'little')&0x3fff);found=True
        elif tag==b'VP8L':
            if len(chunk)<5 or chunk[0]!=0x2f: raise IconError('WebP VP8L 头部无效')
            bits=int.from_bytes(chunk[1:5],'little');dimensions((bits&0x3fff)+1,((bits>>14)&0x3fff)+1);found=True
        elif tag==b'VP8X':
            if len(chunk)!=10: raise IconError('WebP VP8X 头部无效')
            if chunk[0]&2:raise IconError('不支持动态 WebP 图标')
            dimensions(int.from_bytes(chunk[4:7],'little')+1,int.from_bytes(chunk[7:10],'little')+1)
        pos+=8+size+(size%2)
    if pos!=len(b) or not found: raise IconError('WebP 缺少可用像素数据（不支持动态图标）')


def validate_ico(b):
    if len(b)<22 or b[:4]!=b'\0\0\1\0': raise IconError('ICO 头部无效')
    count=int.from_bytes(b[4:6],'little')
    if not 1<=count<=64 or 6+16*count>len(b): raise IconError('ICO 图像目录无效')
    for i in range(count):
        entry=b[6+i*16:22+i*16]
        w,h=entry[0] or 256,entry[1] or 256
        size,offset=struct.unpack('<II',entry[8:16])
        if size<16 or offset<6+16*count or offset+size>len(b): raise IconError('ICO 图像不完整')
        image=b[offset:offset+size]
        if image.startswith(b'\x89PNG'):
            validate_png(image)
            iw,ih=struct.unpack('>II',image[16:24])
            if (iw,ih)!=(w,h): raise IconError('ICO 尺寸不匹配')
        else:
            if len(image)<40: raise IconError('ICO DIB 头部无效')
            head,iw,ih,planes,depth,compression=struct.unpack('<IiiHHI',image[:20])
            if head not in (40,108,124) or head>len(image) or iw!=w or abs(ih)!=h*2 or planes!=1 or depth not in (1,4,8,24,32) or compression:
                raise IconError('ICO 编码不受支持')
            palette=(1<<depth)*4 if depth<=8 else 0
            stride=((w*depth+31)//32)*4
            if len(image)<head+palette+stride*h: raise IconError('ICO 像素数据不完整')


SVG_TAGS = set('svg g path rect circle ellipse line polyline polygon defs linearGradient radialGradient stop clipPath mask pattern symbol use title desc filter feGaussianBlur feOffset feMerge feMergeNode feColorMatrix feBlend feComposite feFlood'.split())


def validate_svg(b):
    try: source=b.decode('utf-8-sig')
    except UnicodeError as e: raise IconError('SVG 必须使用 UTF-8 编码') from e
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)',source,re.I) or re.search(r'<\?(?!xml\s)',source,re.I):
        raise IconError('SVG 不允许 DTD、实体或外部处理指令')
    try:
        parser=ET.iterparse(io.StringIO(source),events=('start','end'))
        depth=0;count=0
        for event,node in parser:
            if event=='start':
                depth+=1;count+=1
                if depth>64 or count>10000 or len(node.attrib)>64:
                    raise IconError('SVG 结构过于复杂（最多 10000 节点、64 层）')
            else:depth-=1
        root=parser.root
    except ET.ParseError as e: raise IconError('SVG XML 格式无效') from e
    if root.tag not in ('svg','{http://www.w3.org/2000/svg}svg'):
        raise IconError('文件不是 SVG 图像')
    for node in root.iter():
        tag=node.tag
        if not isinstance(tag,str): raise IconError('SVG 节点无效')
        if tag.startswith('{'):
            ns,tag=tag[1:].split('}',1)
            if ns!='http://www.w3.org/2000/svg': raise IconError('SVG 不允许外部命名空间内容')
        if tag not in SVG_TAGS: raise IconError('SVG 含不允许的活动或外部内容')
        for key,value in node.attrib.items():
            local=key.rsplit('}',1)[-1].lower(); low=value.lower()
            if local.startswith('on') or local in ('src','base') or '\\' in value or '@' in value or '//' in value or re.search(r'(?:image-set|cross-fade|paint|element|var|attr)\s*\(', low):
                raise IconError('SVG 不允许事件、脚本或外部资源')
            if key.startswith('{') and key.split('}')[0] not in ('{http://www.w3.org/1999/xlink', '{http://www.w3.org/XML/1998/namespace'):
                raise IconError('SVG 属性命名空间不受支持')
            if re.search(r'(?:javascript|vbscript|data|https?|file)\s*:',low) or 'expression(' in low:
                raise IconError('SVG 不允许外部地址或脚本')
            if local=='href' and not re.fullmatch(r'#[A-Za-z0-9_.:-]+',value):
                raise IconError('SVG 引用只能指向图内元素')
            for target in re.findall(r'url\s*\(([^)]*)\)',value,re.I):
                if not re.fullmatch(r'#[A-Za-z0-9_.:-]+',target.strip(' \t\r\n\"\'')):
                    raise IconError('SVG 不允许外部样式资源')
            if 'url' in low and not re.search(r'url\s*\(',low):
                raise IconError('SVG 样式引用无效')


def validate_image(b):
    if not isinstance(b,bytes) or not 24<=len(b)<=MAX_BYTES:
        raise IconError('图标须为 24 字节至 1 MiB 的图片')
    try:
        if b.startswith(b'\x89PNG'): validate_png(b);return '.png'
        if b.startswith(b'\xff\xd8'): validate_jpeg(b);return '.jpg'
        if b.startswith(b'RIFF'): validate_webp(b);return '.webp'
        if b.startswith(b'\0\0\1\0'): validate_ico(b);return '.ico'
        if b.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'<'): validate_svg(b);return '.svg'
    except (struct.error,OverflowError,IndexError) as e:
        raise IconError('图标文件结构无效') from e
    raise IconError('仅支持 PNG、JPG、WebP、SVG、ICO 图标')


def checked_directory(base, parts=(), create=False):
    base=Path(base).absolute()
    if base.is_symlink() or (base.exists() and not base.is_dir()):
        raise IconError('图标根目录不能为符号链接')
    current=base
    for part in parts:
        if part in ('','..','.') or '/' in part: raise IconError('图标路径无效')
        current=current/part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise IconError('图标目录不能为符号链接或普通文件')
        if create and not current.exists(): current.mkdir(mode=0o700)
    return current


def library_dir(data, create=False):
    return checked_directory(data, ('media','icons','library'),create)


def safe_file(directory, name):
    path=Path(directory)/name
    if path.is_symlink(): raise IconError('图标文件不能为符号链接')
    if not path.is_file(): raise IconError('图标不存在，请重新上传或选择')
    return path


def resolve_reference(value, data, static, missing_ok=False):
    if not isinstance(value,str) or len(value)>2048:
        raise IconError('图标引用必须为本站图标路径')
    value=value.strip()
    if not value: return ''
    try:
        if value.startswith('/assets/icons/'):
            name=value[len('/assets/icons/'):]
            if not BUILTIN.fullmatch(name): raise IconError('内置图标路径无效')
            safe_file(checked_directory(static,('icons',)),name)
            return '/assets/icons/'+name
        name=value[len('/media/icons/'):] if value.startswith('/media/icons/') else value
        if not NAME.fullmatch(name): raise IconError('图标引用无效，请上传或选择本站图标')
        safe_file(library_dir(data),name)
        return '/media/icons/'+name
    except IconError:
        if missing_ok:return ''
        raise


def list_icons(data):
    directory=library_dir(data)
    if not directory.exists():return []
    return sorted(p.name for p in directory.iterdir() if NAME.fullmatch(p.name) and p.is_file() and not p.is_symlink())


def store_image(data, b):
    ext=validate_image(b)
    name=hashlib.sha256(b).hexdigest()[:32]+ext
    directory=library_dir(data,True)
    target=directory/name
    if target.is_symlink():raise IconError('图标文件不能为符号链接')
    if target.exists():
        if target.read_bytes()!=b:raise IconError('图标哈希冲突')
        return name
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)
    temporary=directory/('.upload-'+secrets.token_hex(16))
    fd=os.open(temporary,flags,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(b);f.flush();os.fsync(f.fileno())
        # Both absolute paths are scoped, verified descendants of the same library.
        if temporary.parent!=directory or target.parent!=directory or target.is_symlink():
            raise IconError('图标写入路径无效')
        os.replace(temporary,target)
    finally:
        if temporary.exists() and temporary.parent==directory:temporary.unlink()
    return name


def allowed_address(value):
    ip=ipaddress.ip_address(value)
    if isinstance(ip,ipaddress.IPv6Address):
        if ip == ipaddress.ip_address('fd00:ec2::254') or ip.sixtofour is not None or ip.teredo is not None:
            return False
        if ip.ipv4_mapped:ip=ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:return False
    private = ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16') if ip.version==4 else ('fc00::/7',)
    return ip.is_global or any(ip in ipaddress.ip_network(net) for net in private)


def validate_target(target):
    if not isinstance(target,str) or len(target)>2048 or any(ord(c)<=32 or ord(c)==127 for c in target) or '\\' in target:
        raise IconError('请输入有效的 HTTP(S) 图标地址')
    try:
        u=urlsplit(target)
        if u.scheme not in ('http','https') or not u.hostname or u.username is not None or u.password is not None or u.fragment:
            raise ValueError()
        host=u.hostname.encode('idna').decode('ascii')
        if '%' in host or not re.fullmatch(r'[A-Za-z0-9.:-]+',host):raise ValueError()
        port=u.port or (443 if u.scheme=='https' else 80)
        if not 1<=port<=65535:raise ValueError()
        path=u.path or '/'
        if u.query:path+='?'+u.query
        path.encode('ascii')
    except (ValueError,UnicodeError) as e:
        raise IconError('图标地址不能包含凭据、无效端口或非 HTTP(S) 协议') from e
    return u,host,port,path


def resolve_target(host,port):
    future=DNS_POOL.submit(socket.getaddrinfo,host,port,0,socket.SOCK_STREAM)
    try:rows=future.result(timeout=6)
    except (concurrent.futures.TimeoutError,OSError) as e:
        future.cancel();raise IconError('图标主机解析失败或超时') from e
    addresses=list(dict.fromkeys(r[4][0] for r in rows))
    if not addresses or any(not allowed_address(address) for address in addresses):
        raise IconError('不允许访问回环、链路本地、云元数据或保留地址')
    return addresses[0]


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host,port,ip,timeout):
        super().__init__(host,port,timeout=timeout);self.pinned_ip=ip
    def connect(self):
        self.sock=socket.create_connection((self.pinned_ip,self.port),self.timeout)


class PinnedHTTPS(PinnedHTTP):
    def connect(self):
        super().connect()
        self.sock=ssl.create_default_context().wrap_socket(self.sock,server_hostname=self.host)


def fetch_remote(target):
    if not FETCH_SLOTS.acquire(blocking=False):raise IconError('图标抓取繁忙，请稍后重试')
    deadline=time.monotonic()+20
    try:
        for hop in range(5):
            u,host,port,path=validate_target(target)
            ip=resolve_target(host,port)
            remaining=deadline-time.monotonic()
            if remaining<=0:raise IconError('图标抓取超时')
            conn=(PinnedHTTPS if u.scheme=='https' else PinnedHTTP)(host,port,ip,min(8,remaining))
            def expire(connection=conn):
                sock=connection.sock
                if sock:
                    try:sock.shutdown(socket.SHUT_RDWR)
                    except OSError:pass
            timer=threading.Timer(remaining,expire);timer.daemon=True;timer.start()
            try:
                conn.connect()
                conn.putrequest('GET',path,skip_host=True,skip_accept_encoding=True)
                host_header='['+host+']' if ':' in host else host
                if port!=(443 if u.scheme=='https' else 80):host_header+=':'+str(port)
                conn.putheader('Host',host_header)
                conn.putheader('Accept','image/png,image/jpeg,image/webp,image/svg+xml,image/x-icon,image/vnd.microsoft.icon')
                conn.putheader('Accept-Encoding','identity')
                conn.putheader('User-Agent','HomeHub-IconImport/1.0')
                conn.endheaders()
                response=conn.getresponse()
                if response.status in (301,302,303,307,308):
                    location=response.getheader('Location')
                    if not location or hop==4:raise IconError('图标重定向次数过多或地址无效')
                    target=urljoin(target,location)
                    # Every redirect is revalidated and independently DNS-pinned.
                    validate_target(target)
                    continue
                if response.status!=200:raise IconError('图标地址不可公开访问（需登录或源站不可用）')
                if not (response.getheader('Content-Type') or '').lower().split(';')[0].strip().startswith('image/'):
                    raise IconError('远程地址未返回图片')
                if response.getheader('Content-Encoding','identity').lower() not in ('','identity'):
                    raise IconError('远程图片压缩传输不受支持')
                length=response.getheader('Content-Length')
                if length is not None:
                    try:declared=int(length)
                    except ValueError as e:raise IconError('远程图标长度无效') from e
                    if not 0<=declared<=MAX_BYTES:raise IconError('远程图标超过 1 MiB')
                data=bytearray()
                while True:
                    if time.monotonic()>=deadline:raise IconError('图标抓取超时')
                    chunk=response.read1(min(65536,MAX_BYTES+1-len(data)))
                    if not chunk:break
                    data.extend(chunk)
                    if len(data)>MAX_BYTES:raise IconError('远程图标超过 1 MiB')
                if length is not None and len(data)!=declared:raise IconError('远程图标下载不完整')
                return bytes(data)
            except (OSError,http.client.HTTPException) as e:
                raise IconError('图标下载失败或超时，请检查地址与 TLS 证书') from e
            finally:
                timer.cancel();conn.close()
        raise IconError('图标重定向次数过多')
    finally:FETCH_SLOTS.release()

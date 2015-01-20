#!/usr/bin/env python
# coding:utf-8

import sys
import os
import glob

sys.path += glob.glob('%s/*.egg' % os.path.dirname(os.path.abspath(__file__)))

try:
    import gevent
    import gevent.socket
    import gevent.server
    import gevent.queue
    import gevent.event
    import gevent.monkey
    gevent.monkey.patch_all(subprocess=True)
except ImportError:
    gevent = None

import base64
import time
import re
import logging
import thread
import BaseHTTPServer
import urllib2
import urlparse
try:
    import OpenSSL
except ImportError:
    OpenSSL = None
try:
    import dnslib
except ImportError:
    dnslib = None


from common import Common
common = Common()

class PacUtil(object):
    """GoAgent Pac Util"""

    @staticmethod
    def update_pacfile(filename):
        listen_ip = common.LISTEN_IP
        autoproxy = '%s:%s' % (listen_ip, common.LISTEN_PORT)
        blackhole = '%s:%s' % (listen_ip, common.PAC_PORT)

        default = 'DIRECT'
        opener = urllib2.build_opener(urllib2.ProxyHandler({'http': autoproxy, 'https': autoproxy}))
        content = ''
        need_update = True
        with open(filename, 'rb') as fp:
            content = fp.read()
        try:
            placeholder = '// AUTO-GENERATED RULES, DO NOT MODIFY!'
            content = content[:content.index(placeholder)+len(placeholder)]
            content = re.sub(r'''blackhole\s*=\s*['"]PROXY [\.\w:]+['"]''', 'blackhole = \'PROXY %s\'' % blackhole, content)
            content = re.sub(r'''autoproxy\s*=\s*['"]PROXY [\.\w:]+['"]''', 'autoproxy = \'PROXY %s\'' % autoproxy, content)
            if content.startswith('//'):
                line = '// Proxy Auto-Config file generated by autoproxy2pac, %s\r\n' % time.strftime('%Y-%m-%d %H:%M:%S')
                content = line + '\r\n'.join(content.splitlines()[1:])
        except ValueError:
            need_update = False

        try:
            if common.PAC_ADBLOCK:
                logging.info('try download %r to update_pacfile(%r)', common.PAC_ADBLOCK, filename)
                adblock_content = opener.open(common.PAC_ADBLOCK).read()
                logging.info('%r downloaded, try convert it with adblock2pac', common.PAC_ADBLOCK)
                if 'gevent' in sys.modules and time.sleep is getattr(sys.modules['gevent'], 'sleep', None) and hasattr(gevent.get_hub(), 'threadpool'):
                    jsrule = gevent.get_hub().threadpool.apply_e(Exception, PacUtil.adblock2pac, (adblock_content, 'FindProxyForURLByAdblock', blackhole, default))
                else:
                    jsrule = PacUtil.adblock2pac(adblock_content, 'FindProxyForURLByAdblock', blackhole, default)
                content += '\r\n' + jsrule + '\r\n'
                logging.info('%r downloaded and parsed', common.PAC_ADBLOCK)
            else:
                content += '\r\nfunction FindProxyForURLByAdblock(url, host) {return "DIRECT";}\r\n'
        except Exception as e:
            need_update = False
            logging.exception('update_pacfile failed: %r', e)
        except:
            return

        try:
            logging.info('try download %r to update_pacfile(%r)', common.PAC_GFWLIST, filename)
            autoproxy_content = base64.b64decode(opener.open(common.PAC_GFWLIST).read())
            logging.info('%r downloaded, try convert it with autoproxy2pac', common.PAC_GFWLIST)
            if 'gevent' in sys.modules and time.sleep is getattr(sys.modules['gevent'], 'sleep', None) and hasattr(gevent.get_hub(), 'threadpool'):
                jsrule = gevent.get_hub().threadpool.apply_e(Exception, PacUtil.autoproxy2pac, (autoproxy_content, 'FindProxyForURLByAutoProxy', autoproxy, default))
            else:
                jsrule = PacUtil.autoproxy2pac(autoproxy_content, 'FindProxyForURLByAutoProxy', autoproxy, default)
            content += '\r\n' + jsrule + '\r\n'
            logging.info('%r downloaded and parsed', common.PAC_GFWLIST)
        except Exception as e:
            need_update = False
            logging.exception('update_pacfile failed: %r', e)
        except:
            return
        if need_update:
            with open(filename, 'wb') as fp:
                fp.write(content)
            logging.info('%r successfully updated', filename)

    @staticmethod
    def autoproxy2pac(content, func_name='FindProxyForURLByAutoProxy', proxy='127.0.0.1:8087', default='DIRECT', indent=4):
        """Autoproxy to Pac, based on https://github.com/iamamac/autoproxy2pac"""
        jsLines = []
        for line in content.splitlines()[1:]:
            if line and not line.startswith("!"):
                use_proxy = True
                if line.startswith("@@"):
                    line = line[2:]
                    use_proxy = False
                return_proxy = 'PROXY %s' % proxy if use_proxy else default
                if line.startswith('/') and line.endswith('/'):
                    jsLine = 'if (/%s/i.test(url)) return "%s";' % (line[1:-1], return_proxy)
                elif line.startswith('||'):
                    domain = line[2:].lstrip('.')
                    if len(jsLines) > 0 and ('host.indexOf(".%s") >= 0' % domain in jsLines[-1] or 'host.indexOf("%s") >= 0' % domain in jsLines[-1]):
                        jsLines.pop()
                    jsLine = 'if (dnsDomainIs(host, ".%s") || host == "%s") return "%s";' % (domain, domain, return_proxy)
                elif line.startswith('|'):
                    jsLine = 'if (url.indexOf("%s") == 0) return "%s";' % (line[1:], return_proxy)
                elif '*' in line:
                    jsLine = 'if (shExpMatch(url, "*%s*")) return "%s";' % (line.strip('*'), return_proxy)
                elif '/' not in line:
                    jsLine = 'if (host.indexOf("%s") >= 0) return "%s";' % (line, return_proxy)
                else:
                    jsLine = 'if (url.indexOf("%s") >= 0) return "%s";' % (line, return_proxy)
                jsLine = ' ' * indent + jsLine
                if use_proxy:
                    jsLines.append(jsLine)
                else:
                    jsLines.insert(0, jsLine)
        function = 'function %s(url, host) {\r\n%s\r\n%sreturn "%s";\r\n}' % (func_name, '\n'.join(jsLines), ' '*indent, default)
        return function

    @staticmethod
    def urlfilter2pac(content, func_name='FindProxyForURLByUrlfilter', proxy='127.0.0.1:8086', default='DIRECT', indent=4):
        """urlfilter.ini to Pac, based on https://github.com/iamamac/autoproxy2pac"""
        jsLines = []
        for line in content[content.index('[exclude]'):].splitlines()[1:]:
            if line and not line.startswith(';'):
                use_proxy = True
                if line.startswith("@@"):
                    line = line[2:]
                    use_proxy = False
                return_proxy = 'PROXY %s' % proxy if use_proxy else default
                if '*' in line:
                    jsLine = 'if (shExpMatch(url, "%s")) return "%s";' % (line, return_proxy)
                else:
                    jsLine = 'if (url == "%s") return "%s";' % (line, return_proxy)
                jsLine = ' ' * indent + jsLine
                if use_proxy:
                    jsLines.append(jsLine)
                else:
                    jsLines.insert(0, jsLine)
        function = 'function %s(url, host) {\r\n%s\r\n%sreturn "%s";\r\n}' % (func_name, '\n'.join(jsLines), ' '*indent, default)
        return function

    @staticmethod
    def adblock2pac(content, func_name='FindProxyForURLByAdblock', proxy='127.0.0.1:8086', default='DIRECT', indent=4):
        """adblock list to Pac, based on https://github.com/iamamac/autoproxy2pac"""
        jsLines = []
        for line in content.splitlines()[1:]:
            if not line or line.startswith('!') or '##' in line or '#@#' in line:
                continue
            use_proxy = True
            use_start = False
            use_end = False
            use_domain = False
            use_postfix = []
            if '$' in line:
                posfixs = line.split('$')[-1].split(',')
                if any('domain' in x for x in posfixs):
                    continue
                if 'image' in posfixs:
                    use_postfix += ['.jpg', '.gif']
                elif 'script' in posfixs:
                    use_postfix += ['.js']
                else:
                    continue
            line = line.split('$')[0]
            if line.startswith("@@"):
                line = line[2:]
                use_proxy = False
            if '||' == line[:2]:
                line = line[2:]
                if '/' not in line:
                    use_domain = True
                else:
                    if not line.startswith('http://'):
                        line = 'http://' + line
                    use_start = True
            elif '|' == line[0]:
                line = line[1:]
                if not line.startswith('http://'):
                    line = 'http://' + line
                use_start = True
            if line[-1] in ('^', '|'):
                line = line[:-1]
                if not use_postfix:
                    use_end = True
            return_proxy = 'PROXY %s' % proxy if use_proxy else default
            line = line.replace('^', '*').strip('*')
            if use_start and use_end:
                if '*' in line:
                    jsLine = 'if (shExpMatch(url, "%s")) return "%s";' % (line, return_proxy)
                else:
                    jsLine = 'if (url == "%s") return "%s";' % (line, return_proxy)
            elif use_start:
                if '*' in line:
                    if use_postfix:
                        jsCondition = ' || '.join('shExpMatch(url, "%s*%s")' % (line, x) for x in use_postfix)
                        jsLine = 'if (%s) return "%s";' % (jsCondition, return_proxy)
                    else:
                        jsLine = 'if (shExpMatch(url, "%s*")) return "%s";' % (line, return_proxy)
                else:
                    jsLine = 'if (url.indexOf("%s") == 0) return "%s";' % (line, return_proxy)
            elif use_domain and use_end:
                if '*' in line:
                    jsLine = 'if (shExpMatch(host, "%s*")) return "%s";' % (line, return_proxy)
                else:
                    jsLine = 'if (host == "%s") return "%s";' % (line, return_proxy)
            elif use_domain:
                if line.split('/')[0].count('.') <= 1:
                    if use_postfix:
                        jsCondition = ' || '.join('shExpMatch(url, "http://*.%s*%s")' % (line, x) for x in use_postfix)
                        jsLine = 'if (%s) return "%s";' % (jsCondition, return_proxy)
                    else:
                        jsLine = 'if (shExpMatch(url, "http://*.%s*")) return "%s";' % (line, return_proxy)
                else:
                    if '*' in line:
                        if use_postfix:
                            jsCondition = ' || '.join('shExpMatch(url, "http://%s*%s")' % (line, x) for x in use_postfix)
                            jsLine = 'if (%s) return "%s";' % (jsCondition, return_proxy)
                        else:
                            jsLine = 'if (shExpMatch(url, "http://%s*")) return "%s";' % (line, return_proxy)
                    else:
                        if use_postfix:
                            jsCondition = ' || '.join('shExpMatch(url, "http://%s*%s")' % (line, x) for x in use_postfix)
                            jsLine = 'if (%s) return "%s";' % (jsCondition, return_proxy)
                        else:
                            jsLine = 'if (url.indexOf("http://%s") == 0) return "%s";' % (line, return_proxy)
            else:
                if use_postfix:
                    jsCondition = ' || '.join('shExpMatch(url, "*%s*%s")' % (line, x) for x in use_postfix)
                    jsLine = 'if (%s) return "%s";' % (jsCondition, return_proxy)
                else:
                    jsLine = 'if (shExpMatch(url, "*%s*")) return "%s";' % (line, return_proxy)
            jsLine = ' ' * indent + jsLine
            if use_proxy:
                jsLines.append(jsLine)
            else:
                jsLines.insert(0, jsLine)
        function = 'function %s(url, host) {\r\n%s\r\n%sreturn "%s";\r\n}' % (func_name, '\n'.join(jsLines), ' '*indent, default)
        return function


class PACServerHandler(BaseHTTPServer.BaseHTTPRequestHandler):

    pacfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), common.PAC_FILE)
    onepixel = b'GIF89a\x01\x00\x01\x00\x80\xff\x00\xc0\xc0\xc0\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'

    def address_string(self):
        return '%s:%s' % self.client_address[:2]

    def do_CONNECT(self):
        self.wfile.write(b'HTTP/1.1 403\r\nConnection: close\r\n\r\n')

    def do_GET(self):
        path = urlparse.urlparse(self.path).path

        logging.info('PAC %s "%s %s ', self.address_string(), self.command, self.path)
        # check for '..', which will leak file
        if re.search(r'(\.{2})', self.path) is not None:
            self.wfile.write(b'HTTP/1.1 404\r\n\r\n')
            logging.warn('%s %s %s haking', self.address_string(), self.command, self.path )
            return


        filename = os.path.normpath('./' + path)
        if self.path.startswith(('http://', 'https://')):
            data = b'HTTP/1.1 200\r\nCache-Control: max-age=86400\r\nExpires:Oct, 01 Aug 2100 00:00:00 GMT\r\nConnection: close\r\n'
            if filename.endswith(('.jpg', '.gif', '.jpeg', '.bmp')):
                data += b'Content-Type: image/gif\r\n\r\n' + self.onepixel
            else:
                data += b'\r\n'
            self.wfile.write(data)
            logging.info('%s "%s %s HTTP/1.1" 200 -', self.address_string(), self.command, self.path)
        elif os.path.isfile(filename):
            if filename.endswith('.pac'):
                mimetype = 'text/plain'
            else:
                mimetype = 'application/octet-stream'
            if self.path.endswith('.pac?flush'):
                thread.start_new_thread(PacUtil.update_pacfile, (self.pacfile,))
            elif time.time() - os.path.getmtime(self.pacfile) > common.PAC_EXPIRED:
                thread.start_new_thread(lambda: os.utime(self.pacfile, (time.time(), time.time())) or PacUtil.update_pacfile(self.pacfile), tuple())
            self.send_file(filename, mimetype)
        else:
            self.wfile.write(b'HTTP/1.1 404\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n404 Not Found')
            logging.info('%s "%s %s HTTP/1.1" 404 -', self.address_string(), self.command, self.path)

    def send_file(self, filename, mimetype):
        # logging.info('%s "%s %s HTTP/1.1" 200 -', self.address_string(), self.command, self.path)
        data = ''
        with open(filename, 'rb') as fp:
            data = fp.read()
        if data:
            self.wfile.write(('HTTP/1.1 200\r\nContent-Type: %s\r\nContent-Length: %s\r\n\r\n' % (mimetype, len(data))).encode())
            self.wfile.write(data)

#!/usr/bin/python

import sys, os
import math, re, exceptions
import bz2
import cgi,time
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from xml.sax import handler, make_parser, parseString
 
#################### CONSTANTS
OSMHEAD = """<?xml version='1.0' encoding='UTF-8'?>""" \
          """<osm version="0.6" generator="Osmosis 0.32">"""
OSMTAIL = """</osm>"""

VERSION = "0.0.1"


#################### CLASSES
class SubobjectHandler(handler.ContentHandler):
    def __init__(self):
        self.relations = set([])
        self.nodes = set([])
        self.ways = set([])

    def startElement(self, obj, attrs):
        if obj == 'nd':
            self.nodes.add(int(attrs['ref']))
            return
        elif obj == 'member':
            if attrs['type'] == 'relation':
                self.relations.add(int(attrs['ref']))
            elif attrs['type'] == 'way':
                self.ways.add(int(attrs['ref']))
            elif attrs['type'] == 'node':
                self.nodes.add(int(attrs['ref']))

    def endElement(self, obj):
        pass


class Bisect(object):
    def __init__(self, minindex, maxindex):
        self.min = minindex
        self.max = maxindex
        self.reset()

    def reset(self):
        self.increment = 2**int(math.log(self.max - self.min + 1, 2))
        self.cursor = self.min + self.increment - 1
        self.increment /= 2
        return self.cursor
        
    def up(self):
        if not self.increment:
            return None
        self.cursor += self.increment
        self.increment /= 2
        while self.cursor > self.max:
            self.down()
        return self.cursor

    def down(self):        
        if not self.increment:
            return None
        self.cursor -= self.increment
        self.increment /= 2
        return self.cursor


class IndexBlock(object):
    def __init__(self, fileindex):
        self.fileindex = fileindex
        self.first_type = None
        self.first_id = None
        self.valid = False

    def __str__(self):
        return "IndexBlock: fileindex=%s, first_type=%s, first_id=%s, valid=%s" \
            % (self.fileindex, self.first_type, self.first_id, self.valid)


class OsmDb(object):
    def __init__(self, filename):
        self.filename = filename
        self.__filesize = os.path.getsize(self.filename)
        self.__filehandler = open(self.filename, 'rb')
        self.__create_index()

    def __create_index(self):
        CNT = 100000
        self.__index =  [ IndexBlock( i * CNT ) for i in xrange(self.__filesize / CNT - 1 ) ]

    def __validate(self, blk):
        if blk.valid:
            return True

        self.__filehandler.seek(blk.fileindex)
        while True:
            line = self.__filehandler.readline()
            if line == False: ## EOF or Error
                return False
            else:
                for obj in ['node', 'way', 'relation']:
                    if re.match('[ \t]*<%s id="[0-9]*" ' % obj, line):
                        blk.first_type = obj
                        blk.first_id = int(line.split('"')[1])
                        blk.valid = True
                        return True

    def __get_block(self, objtype, objid):
        sortorder = {'node': 0, 'way': 1, 'relation': 2}
        bisect = Bisect(0, len(self.__index)-1)
        blocknr = bisect.reset()
        while True:
            blk = self.__index[blocknr]
            if not self.__validate(blk):
                self.__index.pop(blocknr)
                log("bad block: %s" % blocknr)
                bisect = Bisect(0, len(self.__index)-1)
                blocknr = bisect.reset()
                continue

            log("bisect Nr=%s, seeking %s=%s" %(blocknr, objtype, objid), str(blk))

            res = cmp((sortorder[objtype], objid), 
                      (sortorder.get(blk.first_type, 100), blk.first_id))

            if res < 0:
                if blocknr != 0 and self.__index[blocknr-1].valid:
                    blk2 = self.__index[blocknr-1]
                    if blk2.valid and ((sortorder[objtype], objid) >= \
                                           (sortorder[blk2.first_type], blk2.first_id)):
                        return blk2
                blocknr = bisect.down()
            elif res == 0:   ## exact match (rare case)
                return blk
            else:
                if blocknr == len(self.__index)-1:
                    return blk
                blk2 = self.__index[blocknr+1]
                if blk2.valid and ((sortorder[objtype], objid) < \
                                       (sortorder[blk2.first_type], blk2.first_id)):
                    return blk
                blocknr = bisect.up()

    def print_index(self):
        for i,b in enumerate(self.__index):
            print i, str(b)

    def write_relations(self, filename):
        log("OsmDb: writing relations")

        blk = self.__get_block('relation', 0)
        self.__filehandler.seek(blk.fileindex)

        while True:
            line = self.__filehandler.readline()
            if re.match('[ \t]*<relation id="[0-9]*" ', line):
                break

        if filename[-4] == '.bz2':
            fout = bz2.BZ2File(filename, 'w')
        else:
            fout = open(filename, 'w')
        fout.write(OSMHEAD + '\n' + line)
        while True:
            data = self.__filehandler.read(10000000)
            if not data:
                break
            fout.write(data)
        fout.close()
        log("OsmDb: writing relations complete")

    def write_ways_relations(self, filename):
        log("OsmDb: writing ways and relations")

        blk = self.__get_block('way', 0)
        self.__filehandler.seek(blk.fileindex)

        while True:
            line = self.__filehandler.readline()
            if re.match('[ \t]*<way id="[0-9]*" ', line):
                break

        if filename[-4] == '.bz2':
            fout = bz2.BZ2File(filename, 'w')
        else:
            fout = open(filename, 'w')
        fout.write(OSMHEAD + '\n' + line)
        while True:
            data = self.__filehandler.read(10000000)
            if not data:
                break
            fout.write(data)
        fout.close()
        log("OsmDb: writing ways and relations complete")

    def get_objects_recursive(self, objtype, ids=[], recursive=False):
        relationids = set([])
        wayids = set([])
        nodeids = set([])
        relationdata, waydata, nodedata = '','',''

        if objtype == 'node':
            nodids = set(ids)
        elif objtype == 'way':
            wayids = set(ids)
        elif objtype == 'relation':
            relationids = set(ids)
        else:
            return ""

        if recursive:
            recursions = 100  # maximum recursion level
        else:
            recursions = 1    # only get all direct members
            
        loaded_relationids = set([])
        while relationids:
            r_data = self.get_objects('relation', relationids)
            relationdata += r_data

            if not recursions:
                break
            else:
                recursions -= 1

            parser = make_parser()
            osm_handler = SubobjectHandler()
            parser.setContentHandler(osm_handler)
            parseString(OSMHEAD + r_data + OSMTAIL, osm_handler)
            nodeids |= osm_handler.nodes
            wayids |= osm_handler.ways
            loaded_relationids |= relationids
            relationids = osm_handler.relations - loaded_relationids
            
        if wayids:
            waydata = self.get_objects('way', wayids)
            parser = make_parser()
            osm_handler = SubobjectHandler()
            parser.setContentHandler(osm_handler)
            parseString(OSMHEAD + waydata + OSMTAIL, osm_handler)
            nodeids |= osm_handler.nodes

        if nodeids:
            nodedata = self.get_objects('node', nodeids)

        return nodedata + waydata + relationdata
            

    def get_objects(self, objtype, ids=[]):
        objids = sorted(ids)
        datalines = []
        lastid = objids[0] - 10000
        for objid in objids:
            log(objtype, objid)
            if objid > lastid + 1000:
                blk = self.__get_block(objtype, objid)
                self.__filehandler.seek(blk.fileindex)
            lastid = objid
            while True:
                line = self.__filehandler.readline()
                if re.match('[ \t]*<%s id="[0-9]*" ' % objtype, line):
                    lineid = int(line.split('"')[1])
                    if lineid < objid:
                        continue
                    elif lineid == objid:
                        datalines.append(line)
                        break
                    elif lineid > objid:
                        line = ""
                        print objtype, objid, "not found"
                        lastid = objid - 10000
                        break
            if line == "":
                continue
            if line[-3:] == '/>\n':
                continue
            while True:
                line = self.__filehandler.readline()
                datalines.append(line)
                if re.match('[ \t]*</%s>' %objtype, line):
                    break
        return ''.join(datalines)

class Bz2Reader(object):
    def __init__(self, filehandler, bz2filehead, bz2filesize, bz2reader='python'):
        self.__filehandler = filehandler
        self.__filehead = bz2filehead
        self.__filesize = bz2filesize
        self.__reader = bz2reader

    def changeblock(self, bz2block):
        self.__blk = bz2block
        self.__bz2dc = bz2.BZ2Decompressor()
        self.__bz2dc.decompress(self.__filehead)
        self.__bz2cursor = self.__blk.fileindex
        self.__databuffer = ""
        self.__datacursor = 0

    def readbz2(self, size):
        self.__filehandler.seek(self.__bz2cursor)
        if self.__bz2cursor == self.__filesize - 5:
            return 'EOF'
        if self.__bz2cursor + size >= self.__filesize - 5:
            size = self.__filesize - self.__bz2cursor - 5
        datain = self.__filehandler.read(size)
        while datain:
            try:
                self.__databuffer += self.__bz2dc.decompress(datain)
            except EOFError, msg:
                log(msg, len(self.__bz2dc.unused_data))
                if len(self.__bz2dc.unused_data) > 4:
                    log("unused head", self.__bz2dc.unused_data[:4])
                datain = self.__bz2dc.unused_data
                self.__bz2dc = bz2.BZ2Decompressor()
                continue
            except Exception, msg:
                log(msg)
                return False
            break
        
        self.__bz2cursor = self.__filehandler.tell()
        return True
        
    def read(self, size):
        while (len(self.__databuffer) - self.__datacursor) < size:
            res = self.readbz2(size / 20)
            if res == 'EOF':
                data = self.__databuffer[self.__datacursor:]
                self.__databuffer = ""
                self.__datacusor = 0
                if data:
                    return data
                else:
                    return False
            if not res:
                return False

        data = self.__databuffer[self.__datacursor:self.__datacursor + size]
        self.__datacursor += size

        if self.__datacursor > 2*size:
            self.__databuffer = self.__databuffer[self.__datacursor:]
            self.__datacursor = 0

        return data

    def readline(self):
        while True:
            ind = self.__databuffer.find('\n', self.__datacursor)
            if ind == -1:
                res = self.readbz2(10000)
                if not res:
                    return False
            else:
                line = self.__databuffer[self.__datacursor:ind]
                self.__datacursor = ind + 1
                break

        if self.__datacursor > 100000:
            self.__databuffer = self.__databuffer[self.__datacursor:]
            self.__datacursor = 0
        return line


class Bz2OsmDb(OsmDb):
    def __init__(self, bz2filename):
        self.bz2filename = bz2filename
        self.__index = []

        self.__filesize = os.path.getsize(self.bz2filename)
        self.__filehandler = open(self.bz2filename, 'rb')
        self.__bz2_filehead = self.__filehandler.read(4)
        log("file head:", str(self.__bz2_filehead))

        self.__create_index()
        self.__bz2reader = Bz2Reader(self.__filehandler, self.__bz2_filehead, self.__filesize)

    def __create_index(self):
        BZ2_COMPRESSED_MAGIC = chr(0x31)+chr(0x41)+chr(0x59)+chr(0x26)+chr(0x53)+chr(0x59)
        READBLOCK_SIZE = 100000000
        log("Bz2OsmDb: creating index")
        fin = self.__filehandler
        block_nr = 0
        while True:
            cursor = 0
            fin.seek(block_nr * READBLOCK_SIZE)
            buf = fin.read(READBLOCK_SIZE+10)
            while True:
                found = buf.find(BZ2_COMPRESSED_MAGIC, cursor)
                if found == -1:
                    break
                block = IndexBlock(block_nr * READBLOCK_SIZE + found)
                self.__index.append(block)
                cursor = found + 2
            block_nr += 1
            if fin.tell() < block_nr * READBLOCK_SIZE:
                break

        log("Bz2OsmDb: index complete: %d Blocks" % len(self.__index))


    def __validate(self, blk):
        if blk.valid:
            return True

        self.__bz2reader.changeblock(blk)
        while True:
            line = self.__bz2reader.readline()
            if line == False: ## EOF or Error
                return False
            else:
                for obj in ['node', 'way', 'relation']:
                    if re.match('[ \t]*<%s id="[0-9]*" ' % obj, line):
                        blk.first_type = obj
                        blk.first_id = int(line.split('"')[1])
                        blk.valid = True
                        return True


    def __get_block(self, objtype, objid):
        sortorder = {'node': 0, 'way': 1, 'relation': 2}
        bisect = Bisect(0, len(self.__index)-1)
        blocknr = bisect.reset()
        while True:
            blk = self.__index[blocknr]
            if not self.__validate(blk):
                self.__index.pop(blocknr)
                log("bad block: %s" % blocknr)
                bisect = Bisect(0, len(self.__index)-1)
                blocknr = bisect.reset()
                continue

            log("bisect Nr=%s, seeking %s=%s" %(blocknr, objtype, objid), str(blk))

            res = cmp((sortorder[objtype], objid), 
                      (sortorder.get(blk.first_type, 100), blk.first_id))

            if res < 0:
                if blocknr != 0 and self.__index[blocknr-1].valid:
                    blk2 = self.__index[blocknr-1]
                    if blk2.valid and ((sortorder[objtype], objid) >= \
                                           (sortorder[blk2.first_type], blk2.first_id)):
                        return blk2
                blocknr = bisect.down()
            elif res == 0:   ## exact match (rare case)
                return blk
            else:
                if blocknr == len(self.__index)-1:
                    return blk
                blk2 = self.__index[blocknr+1]
                if blk2.valid and ((sortorder[objtype], objid) < \
                                       (sortorder[blk2.first_type], blk2.first_id)):
                    return blk
                blocknr = bisect.up()

    def write_relations(self, filename):
        log("Bz2OsmDb: writing relations")
        OSMHEAD = """<?xml version='1.0' encoding='UTF-8'?>\n""" \
                  """<osm version="0.6" generator="Osmosis 0.32">"""
        blk = self.__get_block('relation', 0)
        self.__bz2reader.changeblock(blk)

        if filename[-4:] == '.bz2':
            fout = bz2.BZ2File(filename, 'w')
        else:
            fout = open(filename, 'w')

        while True:
            line = self.__bz2reader.readline()
            if re.match('[ \t]*<relation id="[0-9]*" ', line):
                break

        fout.write(OSMHEAD + '\n' + line + '\n')
        while True:
            data = self.__bz2reader.read(10000000)
            if not data:
                break
            fout.write(data)
        fout.close()
        log("Bz2OsmDb: relation writing complete")

    def write_ways_relations(self, filename):
        log("Bz2OsmDb: writing relations")
        OSMHEAD = """<?xml version='1.0' encoding='UTF-8'?>\n""" \
                  """<osm version="0.6" generator="Osmosis 0.32">"""
        blk = self.__get_block('way', 0)
        self.__bz2reader.changeblock(blk)

        if filename[-4:] == '.bz2':
            fout = bz2.BZ2File(filename, 'w')
        else:
            fout = open(filename, 'w')

        while True:
            line = self.__bz2reader.readline()
            if re.match('[ \t]*<way id="[0-9]*" ', line):
                break

        fout.write(OSMHEAD + '\n' + line + '\n')
        while True:
            data = self.__bz2reader.read(10000000)
            if not data:
                break
            fout.write(data)
        fout.close()
        log("Bz2OsmDb: relation writing complete")

    def get_objects(self, objtype, ids=[]):
        objids = sorted(ids)
        datalines = []
        lastid = objids[0] - 10000
        for objid in objids:
            log(objtype, objid)
            if objid > lastid + 1000:
                blk = self.__get_block(objtype, objid)
                self.__bz2reader.changeblock(blk)
            lastid = objid
            while True:
                line = self.__bz2reader.readline()
                if re.match('[ \t]*<%s id="[0-9]*" ' % objtype, line):
                    lineid = int(line.split('"')[1])
                    if lineid < objid:
                        continue
                    elif lineid == objid:
                        datalines.append(line)
                        break
                    elif lineid > objid:
                        line = ""
                        break
            if line == "":
                continue
            if line[-2:] == '/>':
                continue
            while True:
                line = self.__bz2reader.readline()
                datalines.append(line)
                if re.match('[ \t]*</%s>' %objtype, line):
                    break
        return '\n'.join(datalines)


class OSMHttpHandler(BaseHTTPRequestHandler):

    def print_help(self):
        self.send_response(404)
        self.send_header('Content-type',	'text/html')
        self.end_headers()
        self.wfile.write("OSMDB File interface<br>")
        self.wfile.write("=====================<br><br>")
        self.wfile.write("valid commands are:<br><br>")
        self.wfile.write("  nodes?nodes=id1,id2,...<br>")
        self.wfile.write("  ways?ways=id1,id2,...<br>")
        self.wfile.write("  relations?relations=id1,id2,...<br>")
        self.wfile.write("  ways?ways=id1,id2,...&mode=full<br>")
        self.wfile.write("  relations?relations=id1,id2,...&mode=full")
        self.wfile.write("  relations?relations=id1,id2,...&mode=recursive")
        return

    def do_GET(self):
        print self.path
        osm = self.server.osmdb
        toks = self.path.split('?')
        if len(toks) != 2:
            self.print_help()
            return
        else:
            command = toks[0]
            kvs = toks[1].split('&')
            args = dict([kv.split('=',1) for kv in kvs])
        try:
            if command == '/nodes':
                nodes = [int(n) for n in args['nodes'].split(',')]
                data = osm.get_objects('node', nodes)
            elif command == '/ways':
                ways = [int(n) for n in args['ways'].split(',')]
                if args.get('mode','') == 'full':
                    data = osm.get_objects_recursive('way', ways)
                else:
                    data = osm.get_objects('way', ways)
            elif command == '/relations':
                relations = [int(n) for n in args['relations'].split(',')]
                if args.get('mode','') == 'full':
                    data = osm.get_objects_recursive('relation', relations)
                elif args.get('mode','') == 'recursive':
                    data = osm.get_objects_recursive('relation', relations, recursive=True)
                else:
                    data = osm.get_objects('relation', relations)
            self.send_response(200)
            self.send_header('Content-type',	'text/xml')
            self.end_headers()
            self.wfile.write(OSMHEAD+'\n')
            self.wfile.write(data)
            self.wfile.write('</osm>')
            return
        except IOError:
            self.send_error(404,'File Not Found: %s' % self.path)


################### FUNCTIONS
def runserver(port, osmdb):
    try:
        server = HTTPServer(('', port), OSMHttpHandler)
        server.osmdb = osmdb
        print 'started httpserver...'
        server.serve_forever()
    except KeyboardInterrupt:
        print '^C received, shutting down server'
        server.socket.close()

def log(*args):
    return
    for a in args:
        print a,
    print

def usage():
    print sys.argv[0] + " Version " + VERSION
    print "  -h, --help: print this help information"
    print "  --relations=outfile: split relations from input file"
    print "  --ways_relations=outfile: split ways and relations from input file"
    print "  --server=port: start a http-Server on Port"
    print "Examples:"
    print "  osmdb.py --relations=out.osm.bz2 germany.osm.bz2"
    print "  osmdb.py --ways_relations=/dev/stdout planet-latest.osm"
    print "  osmdb.py --server=8888 germany.osm"


#################### MAIN
if __name__ == '__main__':
    import getopt

    try:
        opts, args = getopt.getopt(sys.argv[1:], 'h',
                                   ['relations=', 'ways_relations=', 'server=', 'help'])
    except getopt.GetoptError:
        usage()
        sys.exit()


    for o, a in opts:
        if o in ['--relations']:
            if len(args) != 1:
                usage()
                sys.exit(-1)
            outfile = a
            if os.path.splitext(args[0])[1] in ['.bz2','.BZ2']:
                osmdb = Bz2OsmDb(args[0])
            else:
                osmdb = OsmDb(args[0])
            osmdb.write_relations(outfile)
            sys.exit()
        elif o in ['--ways_relations']:
            if len(args) != 1:
                usage()
                sys.exit(-1)
            outfile = a
            print os.path.splitext(args[0])
            if os.path.splitext(args[0])[1] in ['.bz2','.BZ2']:
                osmdb = Bz2OsmDb(args[0])
            else:
                osmdb = OsmDb(args[0])
            osmdb.write_ways_relations(outfile)
            sys.exit()
        elif o in ['--server']:
            if len(args) != 1:
                usage()
                sys.exit(-1)
            port = int(a)
            if os.path.splitext(args[0])[1] in ['.bz2','.BZ2']:
                osmdb = Bz2OsmDb(args[0])
            else:
                osmdb = OsmDb(args[0])
            runserver(port, osmdb)
            sys.exit()
        elif o in ['--help']:
            usage()
            sys.exit()

#!/usr/bin/env python3
import os
import sys
import struct
import stat
import binascii
import zlib
import lzo
import cstruct

# Константы JFFS2
JFFS2_OLD_MAGIC_BITMASK = 0x1984
JFFS2_MAGIC_BITMASK = 0x1985

JFFS2_COMPR_NONE = 0x00
JFFS2_COMPR_ZERO = 0x01
JFFS2_COMPR_ZLIB = 0x06
JFFS2_COMPR_LZO = 0x07
JFFS2_COMPR_LZMA = 0x08

JFFS2_FEATURE_INCOMPAT = 0xC000
JFFS2_NODE_ACCURATE = 0x2000

JFFS2_NODETYPE_DIRENT = JFFS2_FEATURE_INCOMPAT | JFFS2_NODE_ACCURATE | 1
JFFS2_NODETYPE_INODE = JFFS2_FEATURE_INCOMPAT | JFFS2_NODE_ACCURATE | 2

def mtd_crc(data):
    return (binascii.crc32(data, -1) ^ -1) & 0xFFFFFFFF

def PAD(x):
    return (x + 3) & ~3

cstruct.typedef("uint8", "uint8_t")
cstruct.typedef("uint16", "jint16_t")
cstruct.typedef("uint32", "jint32_t")
cstruct.typedef("uint32", "jmode_t")

class Jffs2_unknown_node(cstruct.CStruct):
    __byte_order__ = cstruct.LITTLE_ENDIAN
    __def__ = """
    struct {
        jint16_t magic;
        jint16_t nodetype;
        jint32_t totlen;
        jint32_t hdr_crc;
    }
    """
    def unpack(self, data):
        cstruct.CStruct.unpack(self, data[:self.size])
        # hdr_crc_match проверяем CRC заголовка без последних 4 байт hdr_crc
        comp_crc = mtd_crc(data[:self.size - 4])
        self.hdr_crc_match = (comp_crc == self.hdr_crc)

class Jffs2_raw_dirent(cstruct.CStruct):
    __byte_order__ = cstruct.LITTLE_ENDIAN
    __def__ = """
    struct {
        jint16_t magic;
        jint16_t nodetype;
        jint32_t totlen;
        jint32_t hdr_crc;
        jint32_t pino;
        jint32_t version;
        jint32_t ino;
        jint32_t mctime;
        uint8_t nsize;
        uint8_t type;
        uint8_t unused[2];
        jint32_t node_crc;
        jint32_t name_crc;
    }
    """
    def unpack(self, data, node_offset):
        cstruct.CStruct.unpack(self, data[:self.size])
        self.name = data[self.size:self.size+self.nsize]  # bytes, без tobytes()
        self.node_offset = node_offset
        self.node_crc_match = (mtd_crc(data[:self.size-8]) == self.node_crc)
        self.name_crc_match = (mtd_crc(self.name) == self.name_crc)

class Jffs2_raw_inode(cstruct.CStruct):
    __byte_order__ = cstruct.LITTLE_ENDIAN
    __def__ = """
    struct {
        jint16_t magic;
        jint16_t nodetype;
        jint32_t totlen;
        jint32_t hdr_crc;
        jint32_t ino;
        jint32_t version;
        jmode_t mode;
        jint16_t uid;
        jint16_t gid;
        jint32_t isize;
        jint32_t atime;
        jint32_t mtime;
        jint32_t ctime;
        jint32_t offset;
        jint32_t csize;
        jint32_t dsize;
        uint8_t compr;
        uint8_t usercompr;
        jint16_t flags;
        jint32_t data_crc;
        jint32_t node_crc;
    }
    """
    def unpack(self, data):
        cstruct.CStruct.unpack(self, data[:self.size])
        node_data = data[self.size:self.size+self.csize]  # bytes уже, без tobytes()
        try:
            if self.compr == JFFS2_COMPR_NONE:
                self.data = node_data
            elif self.compr == JFFS2_COMPR_ZERO:
                self.data = b"\x00" * self.dsize
            elif self.compr == JFFS2_COMPR_ZLIB:
                self.data = zlib.decompress(node_data)
            elif self.compr == JFFS2_COMPR_LZO:
                self.data = lzo.decompress(node_data, False, self.dsize)
            elif self.compr == JFFS2_COMPR_LZMA:
                import jefferson.jffs2_lzma as jffs2_lzma
                self.data = jffs2_lzma.decompress(node_data, self.dsize)
            else:
                self.data = node_data
        except Exception as e:
            print(f"Decompression error on inode {self.ino}: {e}", file=sys.stderr)
            self.data = b"\x00" * self.dsize

def is_safe_path(base_dir, path):
    base_dir = os.path.realpath(base_dir)
    path = os.path.realpath(path)
    return os.path.commonpath([base_dir]) == os.path.commonpath([base_dir, path])

def ensure_dir(path):
    parts = []
    head = ""
    for part in path.split(os.sep):
        if not part:
            continue
        head = os.path.join(head, part)
        parts.append(head)

    for part_path in parts:
        if os.path.exists(part_path) and not os.path.isdir(part_path):
            print(f"Удаляем файл, мешающий созданию папки: {part_path}")
            os.remove(part_path)
        if not os.path.exists(part_path):
            os.mkdir(part_path)

def dump_fs(fs, target):
    node_dict = {}
    for dirent in fs['dirents'].values():
        dirent.inodes = fs['inodes'].get(dirent.ino, [])
        node_dict[dirent.ino] = dirent

    for dirent in fs['dirents'].values():
        # Восстанавливаем путь
        path_parts = []
        pino = dirent.pino
        for _ in range(100):
            if pino not in node_dict:
                break
            pnode = node_dict[pino]
            path_parts.append(pnode.name.decode(errors="ignore"))
            pino = pnode.pino
        path_parts.reverse()
        path_parts.append(dirent.name.decode(errors="ignore"))
        full_path = os.path.join(target, *path_parts)

        if not is_safe_path(target, full_path):
            print(f"Опасный путь: {full_path}, пропускаем")
            continue

        for inode in dirent.inodes:
            mode = inode.mode
            try:
                if stat.S_ISDIR(mode):
                    if os.path.exists(full_path) and not os.path.isdir(full_path):
                        print(f"Удаляем файл, мешающий созданию каталога: {full_path}")
                        os.remove(full_path)
                    if not os.path.isdir(full_path):
                        print(f"Создаём каталог: {full_path}")
                        os.makedirs(full_path)
                elif stat.S_ISREG(mode):
                    # Собираем все фрагменты по их смещению и записываем в один файл
                    chunks = sorted(dirent.inodes, key=lambda i: i.offset)
                    ensure_dir(os.path.dirname(full_path))
                    print(f"Записываем файл: {full_path}")
                    with open(full_path, 'wb') as f:
                        for chunk in chunks:
                            # если есть проверка CRC, можно её вставить здесь
                            # if hasattr(chunk, 'data_crc_match') and not chunk.data_crc_match:
                            #     print(f"  Пропускаем фрагмент offset={chunk.offset}: CRC несовпадает")
                            #     continue
                            f.seek(chunk.offset)
                            f.write(chunk.data)
                    os.chmod(full_path, stat.S_IMODE(mode))
                    break  # один файл на dirent
                elif stat.S_ISLNK(mode):
                    if os.path.exists(full_path):
                        continue
                    print(f"Создаём ссылку: {full_path}")
                    os.symlink(inode.data, full_path)
                else:
                    print(f"Пропускаем тип файла mode={mode:o} для {full_path}")
            except Exception as e:
                print(f"Ошибка файловой операции: {e} для {full_path}")


def scan_fs(content):
    pos = 0
    fs = {'dirents': {}, 'inodes': {}}
    content_len = len(content)
    magic_le = struct.pack("<H", JFFS2_MAGIC_BITMASK)
    magic_old_le = struct.pack("<H", JFFS2_OLD_MAGIC_BITMASK)

    while True:
        idx = content.find(magic_le, pos)
        idx_old = content.find(magic_old_le, pos)
        if idx == -1 and idx_old == -1:
            break
        if idx == -1 or (idx_old != -1 and idx_old < idx):
            idx = idx_old
        pos = idx

        unknown = Jffs2_unknown_node()
        unknown.unpack(content[pos:pos+unknown.size])
        if not unknown.hdr_crc_match:
            pos += 1
            continue

        nodetype = unknown.nodetype
        totlen = unknown.totlen
        node_data = content[pos:pos+totlen]

        if nodetype == JFFS2_NODETYPE_DIRENT:
            dirent = Jffs2_raw_dirent()
            dirent.unpack(node_data, pos)
            if dirent.ino not in fs['dirents'] or fs['dirents'][dirent.ino].version < dirent.version:
                fs['dirents'][dirent.ino] = dirent
        elif nodetype == JFFS2_NODETYPE_INODE:
            inode = Jffs2_raw_inode()
            inode.unpack(node_data)
            fs['inodes'].setdefault(inode.ino, []).append(inode)
        pos += PAD(totlen)
    return fs

def main():
    if len(sys.argv) != 3:
        print(f"Использование: {sys.argv[0]} <jffs2.img> <директория_для_распаковки>")
        sys.exit(1)

    img_path = sys.argv[1]
    out_dir = sys.argv[2]

    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        print(f"Ошибка: {out_dir} существует и не является директорией")
        sys.exit(1)
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    with open(img_path, "rb") as f:
        content = f.read()

    fs = scan_fs(content)
    dump_fs(fs, out_dir)
    print("Распаковка завершена.")

if __name__ == "__main__":
    main()

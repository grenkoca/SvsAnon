#!/usr/bin/python
#
#  Delete the label image from an Aperio SVS file.
#
#  Original by CMU, modified Caleb Grenko
#  cagrenko@davidson.edu
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Lesser General Public License as
#  published by the Free Software Foundation, version 2.1.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#  GNU Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this program. If not, see
#  <http://www.gnu.org/licenses/>.
#

import string
import struct
import sys
import os
import argparse

ASCII = 2
SHORT = 3
LONG = 4
LONG8 = 16

IMAGE_DESCRIPTION = 270
STRIP_OFFSETS = 273
STRIP_BYTE_COUNTS = 279

class TiffFile(file):
    def __init__(self, path):
        file.__init__(self, path, 'r+b')

        # Check header, decide endianness
        endian = self.read(2)
        if endian == 'II':
            self._fmt_prefix = '<'
        elif endian == 'MM':
            self._fmt_prefix = '>'
        else:
            raise IOError('Not a TIFF file')

        # Check TIFF version
        self._bigtiff = False
        version = self.read_fmt('H')
        if version == 42:
            pass
        elif version == 43:
            self._bigtiff = True
            magic2, reserved = self.read_fmt('HH')
            if magic2 != 8 or reserved != 0:
                raise IOError('Bad BigTIFF header')
        else:
            raise IOError('Not a TIFF file')

        # Read directories
        self.directories = []
        while True:
            in_pointer_offset = self.tell()
            directory_offset = self.read_fmt('Z')
            if directory_offset == 0:
                break
            self.seek(directory_offset)
            self.directories.append(TiffDirectory(self, in_pointer_offset))

    def _convert_format(self, fmt):
        # Format strings can have special characters:
        # y: 16-bit   signed on little TIFF, 64-bit   signed on BigTIFF
        # Y: 16-bit unsigned on little TIFF, 64-bit unsigned on BigTIFF
        # z: 32-bit   signed on little TIFF, 64-bit   signed on BigTIFF
        # Z: 32-bit unsigned on little TIFF, 64-bit unsigned on BigTIFF
        if self._bigtiff:
            fmt = fmt.translate(string.maketrans('yYzZ', 'qQqQ'))
        else:
            fmt = fmt.translate(string.maketrans('yYzZ', 'hHiI'))
        return self._fmt_prefix + fmt

    def fmt_size(self, fmt):
        return struct.calcsize(self._convert_format(fmt))

    def read_fmt(self, fmt, force_list=False):
        fmt = self._convert_format(fmt)
        vals = struct.unpack(fmt, self.read(struct.calcsize(fmt)))
        if len(vals) == 1 and not force_list:
            return vals[0]
        else:
            return vals

    def write_fmt(self, fmt, *args):
        fmt = self._convert_format(fmt)
        self.write(struct.pack(fmt, *args))


class TiffDirectory(object):
    def __init__(self, fh, in_pointer_offset):
        self.in_pointer_offset = in_pointer_offset
        self.entries = {}
        count = fh.read_fmt('Y')
        for _ in range(count):
            entry = TiffEntry(fh)
            self.entries[entry.tag] = entry
        self.out_pointer_offset = fh.tell()


class TiffEntry(object):
    def __init__(self, fh):
        self.start = fh.tell()
        self.tag, self.type, self.count, self.value_offset = \
                fh.read_fmt('HHZZ')
        self._fh = fh

    def value(self):
        if self.type == ASCII:
            item_fmt = 'c'
        elif self.type == SHORT:
            item_fmt = 'H'
        elif self.type == LONG:
            item_fmt = 'I'
        elif self.type == LONG8:
            item_fmt = 'Q'
        else:
            raise ValueError('Unsupported type')

        fmt = '%d%s' % (self.count, item_fmt)
        len = self._fh.fmt_size(fmt)
        if len <= self._fh.fmt_size('Z'):
            # Inline value
            self._fh.seek(self.start + self._fh.fmt_size('HHZ'))
        else:
            # Out-of-line value
            self._fh.seek(self.value_offset)
        items = self._fh.read_fmt(fmt, force_list=True)
        if self.type == ASCII:
            if items[-1] != '\0':
                raise ValueError('String not null-terminated')
            return ''.join(items[:-1])
        else:
            return items


def delete_aperio_label(filename, delete_entry=True):
    write_size = 0
    with TiffFile(filename) as fh:
        for directory in fh.directories:
            # Check ImageDescription
            try:
                desc = directory.entries[IMAGE_DESCRIPTION].value()
            except KeyError:
                continue
            if not desc.startswith('Aperio'):
                # Not an Aperio directory
                continue
            lines = desc.splitlines()
            if len(lines) < 2 or not lines[1].startswith('label '):
                # Not the label
                continue

            # Get strip offsets/lengths
            try:
                offsets = directory.entries[STRIP_OFFSETS].value()
                lengths = directory.entries[STRIP_BYTE_COUNTS].value()
            except KeyError:
                print(lines)
                raise IOError('Label is not stripped')

            # Wipe strips
            for offset, length in zip(offsets, lengths):
                fh.seek(offset)
                fh.write('\0' * length)
                write_size += sys.getsizeof('\0' * length)

            # Delete directory
            fh.seek(directory.out_pointer_offset)
            out_pointer = fh.read_fmt('Z')
            fh.seek(directory.in_pointer_offset)
            if delete_entry:
                fh.write_fmt('Z', out_pointer)
            print "Wrote " + str(write_size) + " bytes (" + str(write_size / 1000) + " kb)"
            # Done
            break
        else:
            raise IOError("Couldn't find Aperio label directory")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-manifest",
        "-m",
        help="path to a .csv containing a single column of paths to .svs files",
        type=str,
        default=None
    )

    parser.add_argument(
        "-folder",
        "-f",
        help="File/folder containing .svs files",
        type=str,
        default=None
    )

    parser.add_argument(
        "--remove_header",
        help="Use this option to remove the label header in addition to writing over the slide",
        default=False,
        action='store_true'

    )

    if len(sys.argv) <= 1:
        print("You need some arguments")
        print("Either enter a manifest of file paths (-m), or point to a folder (-f) containing all slides you want anonymized")
        print("Alternatively, list all .svs files sequentally")
        print("... I feel like this is too many options")
        exit(1)

    paths = []
    if str(sys.argv[1]) is not None and str(sys.argv[1]).endswith(".svs"):
        for arg in sys.argv[1:]:
            if str(arg.endswith(".svs")):
                paths.append(arg)


    args = parser.parse_args()
    if args.folder is not None and args.manifest is not None:
        print("Please enter only a manifest or a folder, not both")
        exit(1)

    if args.folder is not None:
        if not str(args.folder).endswith("/"):
            args.folder += "/"
        for path in os.listdir(args.folder):
            if path.endswith(".svs"):
                paths.append(args.folder + path)

    elif args.manifest is not None:
        with open(args.manifest, "r") as manifest:
            for line in manifest:
                if line.strip().endswith(".svs"):
                    paths.append(line.strip())

    else:
        print("You need some arguments")
        print("Either enter a manifest of file paths (-m), or point to a folder (-f) containing all slides you want anonymized")
        print("Alternatively, list all .svs files sequentally")
        print("... I feel like this is too many options")
        exit(1)

    exit_code = 0
    for filename in paths:
        print("\n" + filename)
        try:
            delete_aperio_label(filename, args.remove_header)
        except Exception as e:
            print "Could not strip label from " + filename
            exit_code = 1
    sys.exit(exit_code)



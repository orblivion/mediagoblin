# GNU MediaGoblin -- federated, autonomous media hosting
# Copyright (C) 2011, 2012 MediaGoblin contributors.  See AUTHORS.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import print_function

import codecs
from collections import OrderedDict
import csv
from itertools import islice
import os

import requests
import six
import traceback

from six.moves.urllib.parse import urlparse

from mediagoblin.db.models import LocalUser, Collection
from mediagoblin.gmg_commands import util as commands_util
from mediagoblin.submit.lib import (
    submit_media, get_upload_file_limits,
    FileUploadLimit, UserUploadLimit, UserPastUploadLimit)
from mediagoblin.tools.metadata import compact_and_validate
from mediagoblin.tools.translate import pass_to_ugettext as _
from jsonschema.exceptions import ValidationError


def parser_setup(subparser):
    subparser.description = """\
This command allows the administrator to upload many media files at once."""
    subparser.epilog = _(u"""For more information about how to properly run this
script (and how to format the metadata csv file), read the MediaGoblin
documentation page on command line uploading
<http://docs.mediagoblin.org/siteadmin/commandline-upload.html>""")
    subparser.add_argument(
        'username',
        help=_(u"Name of user these media entries belong to"))
    subparser.add_argument(
        'metadata_path',
        help=_(
u"""Path to the csv file containing metadata information."""))
    subparser.add_argument(
        '--celery',
        action='store_true',
        help=_("Don't process eagerly, pass off to celery. WARNING: If there is an error during processing (transcoding error, etc) you will not see it here, and this script will continue adding media from the csv. This may be relevant if, for instance, you're adding media to a collection, and the order of entries is important. If such an error were to occur in the middle of your csv, you might have to delete many items before retrying the problem file in order to keep order correct."))
    subparser.add_argument(
        '--start',
        type=int,
        default=1,
        help=_(u"Start with this entry number (not line number!) in the csv. Ex: --start 5 starts with the 5th entry. --start 1 starts with the first entry (ie, default behavior)."))


def _get_collection_slugs(media_data):
    return {
        collection_slug.strip() for collection_slug
        in media_data.get('collections', u'').split(',')
        if collection_slug.strip()
    }


def _get_collections_lookup(user, media_metadata):
    all_collection_slugs = set.union(set(), *(
        _get_collection_slugs(media_data)
        for media_data in media_metadata.itervalues()
    ))
    all_collections_lookup = {
        c.slug: c
        for c in Collection.query.filter_by(
            actor=user.id,
            type=Collection.USER_DEFINED_TYPE,
        ).filter(
            Collection.slug.in_(
                tuple(all_collection_slugs)
            ),
        )
    }
    invalid_collections_slugs = (
        set(all_collection_slugs)
        - set(c.slug for c in all_collections_lookup.itervalues())
    )
    if invalid_collections_slugs:
        raise ValueError(
            'Couldn\'t find these collections: %s'
            % ", ".join(invalid_collections_slugs)
        )
    return all_collections_lookup


def batchaddmedia(args):
    # Run eagerly unless explicetly set not to
    if not args.celery:
        os.environ['CELERY_ALWAYS_EAGER'] = 'true'

    app = commands_util.setup_app(args)
    assert args.start >= 0

    files_uploaded, files_attempted = 0, 0

    # get the user
    user = app.db.LocalUser.query.filter(
        LocalUser.username==args.username.lower()
    ).first()
    if user is None:
        print(_(u"Sorry, no user by username '{username}' exists".format(
                    username=args.username)))
        return

    upload_limit, max_file_size = get_upload_file_limits(user)

    if os.path.isfile(args.metadata_path):
        metadata_path = args.metadata_path
    else:
        error = _(u'File at {path} not found, use -h flag for help'.format(
                    path=args.metadata_path))
        print(error)
        return

    abs_metadata_filename = os.path.abspath(metadata_path)
    abs_metadata_dir = os.path.dirname(abs_metadata_filename)
    upload_limit, max_file_size = get_upload_file_limits(user)

    def maybe_unicodeify(some_string):
        # this is kinda terrible
        if some_string is None:
            return None
        else:
            return six.text_type(some_string)

    with codecs.open(
            abs_metadata_filename, 'r', encoding='utf-8') as all_metadata:
        contents = all_metadata.read()
        media_metadata = parse_csv_file(contents)

    # Grab the collections, or fail before changing anything
    all_collections_lookup = _get_collections_lookup(user, dict(
        islice(media_metadata.iteritems(), args.start - 1, None)
    ))

    for media_id, media_data in islice(media_metadata.iteritems(), args.start - 1, None):
        file_metadata = {
            k:v
            for (k, v)
            in media_data.iteritems()
            if k in {'location',
                     'license',
                     'title',
                     'dc:title',
                     'description',
                     'dc:description'}
        }
        files_attempted += 1
        # In case the metadata was not uploaded initialize an empty dictionary.
        json_ld_metadata = compact_and_validate({})

        # Get all metadata entries starting with 'media' as variables and then
        # delete them because those are for internal use only.
        original_location = file_metadata['location']
        url = urlparse(original_location)
        filename = url.path.split()[-1]

        print(_(u"""Submitting {filename}.
If there is a problem submitting, and you fix it, and you don't edit the csv,
you can continue where you left off by adding this option: --start {start_file_num}
""".format(filename=filename, start_file_num=args.start + files_attempted - 1)))

        ### Pull the important media information for mediagoblin from the
        ### metadata, if it is provided.
        title = file_metadata.get('title') or file_metadata.get('dc:title')
        description = (file_metadata.get('description') or
            file_metadata.get('dc:description'))
        tags_string = media_data.get('tags', u'')

        license = file_metadata.get('license')
        try:
            json_ld_metadata = compact_and_validate(file_metadata)
        except ValidationError as exc:
            error = _(u"""Error with media '{media_id}' value '{error_path}': {error_msg}
Metadata was not uploaded.""".format(
                media_id=media_id,
                error_path=exc.path[0],
                error_msg=exc.message))
            print(error)
            if args.stop_on_error:
                return
            continue

        collections = [
            all_collections_lookup[collection_slug]
            for collection_slug in _get_collection_slugs(media_data)
        ]

        if url.scheme == 'http':
            res = requests.get(url.geturl(), stream=True)
            media_file = res.raw

        elif url.scheme == '':
            path = url.path
            if os.path.isabs(path):
                file_abs_path = os.path.abspath(path)
            else:
                file_path = os.path.join(abs_metadata_dir, path)
                file_abs_path = os.path.abspath(file_path)
            try:
                media_file = file(file_abs_path, 'r')
            except IOError:
                print(_(u"""\
FAIL: Local file {filename} could not be accessed.
{filename} will not be uploaded.""".format(filename=filename)))
                continue
        try:
            submit_media(
                mg_app=app,
                user=user,
                submitted_file=media_file,
                filename=filename,
                title=maybe_unicodeify(title),
                description=maybe_unicodeify(description),
                license=maybe_unicodeify(license),
                metadata=json_ld_metadata,
                tags_string=tags_string,
                upload_limit=upload_limit,
                max_file_size=max_file_size,
                collections=collections)
            print(_(u"""Successfully submitted {filename}!
Be sure to look at the Media Processing Panel on your website to be sure it
uploaded successfully.""".format(filename=filename)))
            files_uploaded += 1
        except FileUploadLimit:
            print(_(
u"FAIL: This file is larger than the upload limits for this site."))
            if args.stop_on_error:
                return
        except UserUploadLimit:
            print(_(
"FAIL: This file will put this user past their upload limits."))
            if args.stop_on_error:
                return
        except UserPastUploadLimit:
            print(_("FAIL: This user is already past their upload limits."))
            if args.stop_on_error:
                return
    print(_(
"{files_uploaded} out of {files_attempted} files successfully submitted".format(
        files_uploaded=files_uploaded,
        files_attempted=files_attempted)))


def unicode_csv_reader(unicode_csv_data, dialect=csv.excel, **kwargs):
    # csv.py doesn't do Unicode; encode temporarily as UTF-8:
    # TODO: this probably won't be necessary in Python 3
    csv_reader = csv.reader(utf_8_encoder(unicode_csv_data),
                            dialect=dialect, **kwargs)
    for row in csv_reader:
        # decode UTF-8 back to Unicode, cell by cell:
        yield [six.text_type(cell, 'utf-8') for cell in row]

def utf_8_encoder(unicode_csv_data):
    for line in unicode_csv_data:
        yield line.encode('utf-8')

def parse_csv_file(file_contents):
    """
    The helper function which converts the csv file into a dictionary where each
    item's key is the provided value 'id' and each item's value is another
    dictionary.
    """
    list_of_contents = file_contents.split('\n')
    key, lines = (list_of_contents[0].split(','),
                  list_of_contents[1:])
    objects_dict = OrderedDict()

    # Build a dictionary
    for index, line in enumerate(lines):
        if line.isspace() or line == u'': continue
        values = unicode_csv_reader([line]).next()
        line_dict = dict([(key[i], val)
            for i, val in enumerate(values)])
        media_id = line_dict.get('id') or index
        objects_dict[media_id] = (line_dict)

    return objects_dict

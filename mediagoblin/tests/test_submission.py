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

import six

if six.PY2:  # this hack only work in Python 2
    import sys
    reload(sys)
    sys.setdefaultencoding('utf-8')

from collections import namedtuple
import os
import pytest

import six.moves.urllib.parse as urlparse

# this gst initialization stuff is really required here
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)

from mediagoblin.tests.tools import fixture_add_user, fixture_add_collection
from .media_tools import create_av
from mediagoblin import mg_globals
from mediagoblin.db.models import MediaEntry, User, LocalUser, Collection
from mediagoblin.db.base import Session
from mediagoblin.gmg_commands.batchaddmedia import batchaddmedia
from mediagoblin.gmg_commands.addmedia import addmedia
from mediagoblin.tools import template
from mediagoblin.media_types.image import ImageMediaManager
from mediagoblin.media_types.pdf.processing import check_prerequisites as pdf_check_prerequisites

from .resources import GOOD_JPG, GOOD_PNG, EVIL_FILE, EVIL_JPG, EVIL_PNG, \
    BIG_BLUE, GOOD_PDF, GPS_JPG, MED_PNG, BIG_PNG

GOOD_TAG_STRING = u'yin,yang'
BAD_TAG_STRING = six.text_type('rage,' + 'f' * 26 + 'u' * 26)

FORM_CONTEXT = ['mediagoblin/submit/start.html', 'submit_form']
REQUEST_CONTEXT = ['mediagoblin/user_pages/user.html', 'request']


class TestSubmission:
    @pytest.fixture(autouse=True)
    def setup(self, test_app):
        self.test_app = test_app

        # TODO: Possibly abstract into a decorator like:
        # @as_authenticated_user('chris')
        fixture_add_user(privileges=[u'active',u'uploader', u'commenter'])

        self.login()

    def our_user(self):
        """
        Fetch the user we're submitting with.  Every .get() or .post()
        invalidates the session; this is a hacky workaround.
        """
        #### FIXME: Pytest collects this as a test and runs this.
        ####   ... it shouldn't.  At least it passes, but that's
        ####   totally stupid.
        ####   Also if we found a way to make this run it should be a
        ####   property.
        return LocalUser.query.filter(LocalUser.username==u'chris').first()

    def login(self):
        self.test_app.post(
            '/auth/login/', {
                'username': u'chris',
                'password': 'toast'})

    def logout(self):
        self.test_app.get('/auth/logout/')

    def do_post(self, data, *context_keys, **kwargs):
        url = kwargs.pop('url', '/submit/')
        do_follow = kwargs.pop('do_follow', False)
        template.clear_test_template_context()
        response = self.test_app.post(url, data, **kwargs)
        if do_follow:
            response.follow()
        context_data = template.TEMPLATE_TEST_CONTEXT
        for key in context_keys:
            context_data = context_data[key]
        return response, context_data

    def upload_data(self, filename):
        return {'upload_files': [('file', filename)]}

    def check_comments(self, request, media_id, count):
        gmr = request.db.GenericModelReference.query.filter_by(
            obj_pk=media_id,
            model_type=request.db.MediaEntry.__tablename__
        ).first()
        if gmr is None and count <= 0:
            return # Yerp it's fine.
        comments = request.db.Comment.query.filter_by(target_id=gmr.id)
        assert count == comments.count()

    def test_missing_fields(self):
        # Test blank form
        # ---------------
        response, form = self.do_post({}, *FORM_CONTEXT)
        assert form.file.errors == [u'You must provide a file.']

        # Test blank file
        # ---------------
        response, form = self.do_post({'title': u'test title'}, *FORM_CONTEXT)
        assert form.file.errors == [u'You must provide a file.']

    def check_url(self, response, path):
        assert urlparse.urlsplit(response.location)[2] == path

    def check_normal_upload(self, title, filename):
        response, context = self.do_post({'title': title}, do_follow=True,
                                         **self.upload_data(filename))
        self.check_url(response, '/u/{0}/'.format(self.our_user().username))
        assert 'mediagoblin/user_pages/user.html' in context
        # Make sure the media view is at least reachable, logged in...
        url = '/u/{0}/m/{1}/'.format(self.our_user().username,
                                     title.lower().replace(' ', '-'))
        self.test_app.get(url)
        # ... and logged out too.
        self.logout()
        self.test_app.get(url)

    def user_upload_limits(self, uploaded=None, upload_limit=None):
        our_user = self.our_user()

        if uploaded:
            our_user.uploaded = uploaded
        if upload_limit:
            our_user.upload_limit = upload_limit

        our_user.save()
        Session.expunge(our_user)

    def test_normal_jpg(self):
        # User uploaded should be 0
        assert self.our_user().uploaded == 0

        self.check_normal_upload(u'Normal upload 1', GOOD_JPG)

        # User uploaded should be the same as GOOD_JPG size in Mb
        file_size = os.stat(GOOD_JPG).st_size / (1024.0 * 1024)
        file_size = float('{0:.2f}'.format(file_size))

        # Reload user
        assert self.our_user().uploaded == file_size

    def test_public_id_populated(self):
        # Upload the image first.
        response, request = self.do_post({'title': u'Balanced Goblin'},
                                         *REQUEST_CONTEXT, do_follow=True,
                                         **self.upload_data(GOOD_JPG))
        media = self.check_media(request, {'title': u'Balanced Goblin'}, 1)

        # Now check that the public_id attribute is set.
        assert media.public_id != None

    def test_normal_png(self):
        self.check_normal_upload(u'Normal upload 2', GOOD_PNG)

    @pytest.mark.skipif("not os.path.exists(GOOD_PDF) or not pdf_check_prerequisites()")
    def test_normal_pdf(self):
        response, context = self.do_post({'title': u'Normal upload 3 (pdf)'},
                                         do_follow=True,
                                         **self.upload_data(GOOD_PDF))
        self.check_url(response, '/u/{0}/'.format(self.our_user().username))
        assert 'mediagoblin/user_pages/user.html' in context

    def test_default_upload_limits(self):
        self.user_upload_limits(uploaded=500)

        # User uploaded should be 500
        assert self.our_user().uploaded == 500

        response, context = self.do_post({'title': u'Normal upload 4'},
                                         do_follow=True,
                                         **self.upload_data(GOOD_JPG))
        self.check_url(response, '/u/{0}/'.format(self.our_user().username))
        assert 'mediagoblin/user_pages/user.html' in context

        # Shouldn't have uploaded
        assert self.our_user().uploaded == 500

    def test_user_upload_limit(self):
        self.user_upload_limits(uploaded=25, upload_limit=25)

        # User uploaded should be 25
        assert self.our_user().uploaded == 25

        response, context = self.do_post({'title': u'Normal upload 5'},
                                         do_follow=True,
                                         **self.upload_data(GOOD_JPG))
        self.check_url(response, '/u/{0}/'.format(self.our_user().username))
        assert 'mediagoblin/user_pages/user.html' in context

        # Shouldn't have uploaded
        assert self.our_user().uploaded == 25

    def test_user_under_limit(self):
        self.user_upload_limits(uploaded=499)

        # User uploaded should be 499
        assert self.our_user().uploaded == 499

        response, context = self.do_post({'title': u'Normal upload 6'},
                                         do_follow=False,
                                         **self.upload_data(MED_PNG))
        form = context['mediagoblin/submit/start.html']['submit_form']
        assert form.file.errors == [u'Sorry, uploading this file will put you'
                                    ' over your upload limit.']

        # Shouldn't have uploaded
        assert self.our_user().uploaded == 499

    def test_big_file(self):
        response, context = self.do_post({'title': u'Normal upload 7'},
                                         do_follow=False,
                                         **self.upload_data(BIG_PNG))

        form = context['mediagoblin/submit/start.html']['submit_form']
        assert form.file.errors == [u'Sorry, the file size is too big.']

    def check_media(self,
                    request,
                    find_data,
                    count=None,
                    tag_slugs=None,
                    collection_slugs=None):
        media = MediaEntry.query.filter_by(**find_data)
        if count is not None:
            assert media.count() == count
            if count == 0:
                return
        for media_item in media:
            if collection_slugs is not None:
                assert set(collection_slugs) == {
                    c.slug for c in media_item.collections}
            if tag_slugs is not None:
                assert set(tag_slugs) == {
                    tag['slug'] for tag in media_item.tags}
        return media[0]

    def test_tags(self):
        # Good tag string
        # --------
        response, request = self.do_post({'title': u'Balanced Goblin 2',
                                          'tags': GOOD_TAG_STRING},
                                         *REQUEST_CONTEXT, do_follow=True,
                                         **self.upload_data(GOOD_JPG))
        media = self.check_media(request, {'title': u'Balanced Goblin 2'}, 1)
        assert media.tags[0]['name'] == u'yin'
        assert media.tags[0]['slug'] == u'yin'

        assert media.tags[1]['name'] == u'yang'
        assert media.tags[1]['slug'] == u'yang'

        # Test tags that are too long
        # ---------------
        response, form = self.do_post({'title': u'Balanced Goblin 2',
                                       'tags': BAD_TAG_STRING},
                                      *FORM_CONTEXT,
                                      **self.upload_data(GOOD_JPG))
        assert form.tags.errors == [
                u'Tags must be shorter than 50 characters.  ' \
                    'Tags that are too long: ' \
                    'ffffffffffffffffffffffffffuuuuuuuuuuuuuuuuuuuuuuuuuu']

    def test_delete(self):
        self.user_upload_limits(uploaded=50)
        response, request = self.do_post({'title': u'Balanced Goblin'},
                                         *REQUEST_CONTEXT, do_follow=True,
                                         **self.upload_data(GOOD_JPG))
        media = self.check_media(request, {'title': u'Balanced Goblin'}, 1)
        media_id = media.id

        # render and post to the edit page.
        edit_url = request.urlgen(
            'mediagoblin.edit.edit_media',
            user=self.our_user().username, media_id=media_id)
        self.test_app.get(edit_url)
        self.test_app.post(edit_url,
            {'title': u'Balanced Goblin',
             'slug': u"Balanced=Goblin",
             'tags': u''})
        media = self.check_media(request, {'title': u'Balanced Goblin'}, 1)
        assert media.slug == u"balanced-goblin"

        # Add a comment, so we can test for its deletion later.
        self.check_comments(request, media_id, 0)
        comment_url = request.urlgen(
            'mediagoblin.user_pages.media_post_comment',
            user=self.our_user().username, media_id=media_id)
        response = self.do_post({'comment_content': 'i love this test'},
                                url=comment_url, do_follow=True)[0]
        self.check_comments(request, media_id, 1)

        # Do not confirm deletion
        # ---------------------------------------------------
        delete_url = request.urlgen(
            'mediagoblin.user_pages.media_confirm_delete',
            user=self.our_user().username, media_id=media_id)
        # Empty data means don't confirm
        response = self.do_post({}, do_follow=True, url=delete_url)[0]
        media = self.check_media(request, {'title': u'Balanced Goblin'}, 1)
        media_id = media.id

        # Confirm deletion
        # ---------------------------------------------------
        response, request = self.do_post({'confirm': 'y'}, *REQUEST_CONTEXT,
                                         do_follow=True, url=delete_url)
        self.check_media(request, {'id': media_id}, 0)
        self.check_comments(request, media_id, 0)

        # Check that user.uploaded is the same as before the upload
        assert self.our_user().uploaded == 50

    def test_evil_file(self):
        # Test non-suppoerted file with non-supported extension
        # -----------------------------------------------------
        response, form = self.do_post({'title': u'Malicious Upload 1'},
                                      *FORM_CONTEXT,
                                      **self.upload_data(EVIL_FILE))
        assert len(form.file.errors) == 1
        assert 'Sorry, I don\'t support that file type :(' == \
                str(form.file.errors[0])


    def test_get_media_manager(self):
        """Test if the get_media_manger function returns sensible things
        """
        response, request = self.do_post({'title': u'Balanced Goblin'},
                                         *REQUEST_CONTEXT, do_follow=True,
                                         **self.upload_data(GOOD_JPG))
        media = self.check_media(request, {'title': u'Balanced Goblin'}, 1)

        assert media.media_type == u'mediagoblin.media_types.image'
        assert isinstance(media.media_manager, ImageMediaManager)
        assert media.media_manager.entry == media


    def test_sniffing(self):
        '''
        Test sniffing mechanism to assert that regular uploads work as intended
        '''
        template.clear_test_template_context()
        response = self.test_app.post(
            '/submit/', {
                'title': u'UNIQUE_TITLE_PLS_DONT_CREATE_OTHER_MEDIA_WITH_THIS_TITLE'
                }, upload_files=[(
                    'file', GOOD_JPG)])

        response.follow()

        context = template.TEMPLATE_TEST_CONTEXT['mediagoblin/user_pages/user.html']

        request = context['request']

        media = request.db.MediaEntry.query.filter_by(
            title=u'UNIQUE_TITLE_PLS_DONT_CREATE_OTHER_MEDIA_WITH_THIS_TITLE').first()

        assert media.media_type == 'mediagoblin.media_types.image'

    def check_false_image(self, title, filename):
        # NOTE: The following 2 tests will ultimately fail, but they
        #   *will* pass the initial form submission step.  Instead,
        #   they'll be caught as failures during the processing step.
        response, context = self.do_post({'title': title}, do_follow=True,
                                         **self.upload_data(filename))
        self.check_url(response, '/u/{0}/'.format(self.our_user().username))
        entry = mg_globals.database.MediaEntry.query.filter_by(title=title).first()
        assert entry.state == 'failed'
        assert entry.fail_error == u'mediagoblin.processing:BadMediaFail'

    def test_evil_jpg(self):
        # Test non-supported file with .jpg extension
        # -------------------------------------------
        self.check_false_image(u'Malicious Upload 2', EVIL_JPG)

    def test_evil_png(self):
        # Test non-supported file with .png extension
        # -------------------------------------------
        self.check_false_image(u'Malicious Upload 3', EVIL_PNG)

    def test_media_data(self):
        self.check_normal_upload(u"With GPS data", GPS_JPG)
        media = self.check_media(None, {"title": u"With GPS data"}, 1)
        assert media.get_location.position["latitude"] == 59.336666666666666

    def test_audio(self):
        with create_av(make_audio=True) as path:
            self.check_normal_upload('Audio', path)

    def test_video(self):
        with create_av(make_video=True) as path:
            self.check_normal_upload('Video', path)

    def test_audio_and_video(self):
        with create_av(make_audio=True, make_video=True) as path:
            self.check_normal_upload('Audio and Video', path)

    def test_processing(self):
        public_store_dir = mg_globals.global_config[
            'storage:publicstore']['base_dir']

        data = {'title': u'Big Blue'}
        response, request = self.do_post(data, *REQUEST_CONTEXT, do_follow=True,
                                         **self.upload_data(BIG_BLUE))
        media = self.check_media(request, data, 1)
        last_size = 1024 ** 3  # Needs to be larger than bigblue.png
        for key, basename in (('original', 'bigblue.png'),
                              ('medium', 'bigblue.medium.png'),
                              ('thumb', 'bigblue.thumbnail.png')):
            # Does the processed image have a good filename?
            filename = os.path.join(
                public_store_dir,
                *media.media_files[key])
            assert filename.endswith('_' + basename)
            # Is it smaller than the last processed image we looked at?
            size = os.stat(filename).st_size
            assert last_size > size
            last_size = size

    def test_gmg_addmedia(self):
        media_filename = 'mediagoblin/tests/test_submission/good.jpg'
        license = 'http://creativecommons.org/publicdomain/zero/1.0/'
        AddMediaArgs = namedtuple('AddMediaArgs', [
            'celery',
            'collections',
            'conf_file',
            'description',
            'filename',
            'license',
            'tags',
            'title',
            'username',
        ])
        # Not initially in DB
        self.check_media(None, {'title': u'Test Title'}, 0)
        self.check_media(None, {'title': u'Test Title No Frills'}, 0)

        # First, successfully add one without collections or tags
        addmedia(
            AddMediaArgs(
                celery=False,
                collections=None,
                conf_file='mediagoblin.ini',
                description='Test Description No Frills',
                filename=media_filename,
                license=license,
                tags=None,
                title='Test Title No Frills',
                username='chris',
            )
        )
        self.check_media(
            None,
            {
                'title': u'Test Title No Frills',
                'description': u'Test Description No Frills',
                'license': license,
            },
            count=1,
            tag_slugs=[],
            collection_slugs=[],
        )

        # Now for the one with collections and tags:
        # Make 1 of the 2 expected collections
        collection_1 = fixture_add_collection(
            name=u"Test Collection 1", user=self.our_user())
        assert collection_1.slug == 'test-collection-1'

        # Fail for Test Collection 2 not existing
        with pytest.raises(ValueError):
            addmedia(
                AddMediaArgs(
                    celery=False,
                    collections='test-collection-1, test-collection-2',
                    conf_file='mediagoblin.ini',
                    description='Test Description',
                    filename=media_filename,
                    license=license,
                    tags='test-tag-1, test-tag-2',
                    title='Test Title',
                    username='chris',
                )
            )
        self.check_media(None, {'title': u'Test Title'}, 0)

        # Make the second expected collection
        collection_2 = fixture_add_collection(
            name=u"Test Collection 2", user=self.our_user())
        assert collection_2.slug == 'test-collection-2'

        # Success!
        addmedia(
            AddMediaArgs(
                celery=False,
                collections='test-collection-1, test-collection-2',
                conf_file='mediagoblin.ini',
                description='Test Description',
                filename=media_filename,
                license=license,
                tags='test-tag-1, test-tag-2',
                title='Test Title',
                username='chris',
            )
        )
        self.check_media(
            None,
            {
                'title': u'Test Title',
                'description': u'Test Description',
                'license': license,
            },
            count=1,
            tag_slugs=[u'test-tag-1', u'test-tag-2'],
            collection_slugs=[collection_1.slug, collection_2.slug]
        )

    def test_gmg_batchaddmedia(self):
        csv_filename = 'mediagoblin/tests/test_submission/test.csv'
        csv_no_collections_or_tags_filename = (
            'mediagoblin/tests/test_submission/test_no_collections_or_tags.csv'
        )
        BatchAddMediaArgs = namedtuple('BatchAddMediaArgs', [
            'celery',
            'conf_file',
            'metadata_path',
            'username',
            'start',
        ])

        # Not initially in DB
        self.check_media(None, {'title': u'Test Title A'}, 0)
        self.check_media(None, {'title': u'Test Title B'}, 0)
        self.check_media(None, {'title': u'Test Title No Frills'}, 0)

        # First, successfully add one without collections or tags
        batchaddmedia(
            BatchAddMediaArgs(
                celery=False,
                conf_file='mediagoblin.ini',
                metadata_path=csv_no_collections_or_tags_filename,
                username='chris',
            )
        )
        self.check_media(
            None,
            {
                'title': u'Test Title No Frills',
                'description': u'Test Description No Frills',
                'license': u'http://creativecommons.org/publicdomain/zero/1.0/',
            },
            count=1,
            tag_slugs=[],
            collection_slugs=[],
        )

        # Now for the ones with collections and tags:
        # Make 2 of the 3 expected collections
        # (Assert expected slugs because they're in the csv)
        collection_1 = fixture_add_collection(
            name=u"Test Collection 1", user=self.our_user())
        assert collection_1.slug == 'test-collection-1'
        collection_2 = fixture_add_collection(
            name=u"Test Collection 2", user=self.our_user())
        assert collection_2.slug == 'test-collection-2'

        # Fail for Test Collection 3 not existing
        with pytest.raises(ValueError):
            batchaddmedia(
                BatchAddMediaArgs(
                    celery=False,
                    conf_file='mediagoblin.ini',
                    metadata_path=csv_filename,
                    username='chris',
                    start=2,
                )
            )
        self.check_media(None, {'title': u'Test Title A'}, 0)
        self.check_media(None, {'title': u'Test Title B'}, 0)
        self.check_media(None, {'title': u'Test Title Should Not Exist'}, 0)

        # Make the final expected collection
        collection_3 = fixture_add_collection(
            name=u"Test Collection 3", user=self.our_user())
        assert collection_3.slug == 'test-collection-3'

        # Success!
        batchaddmedia(
            BatchAddMediaArgs(
                celery=False,
                conf_file='mediagoblin.ini',
                metadata_path=csv_filename,
                username='chris',
                start=2,
            )
        )
        self.check_media(
            None,
            {
                'title': u'Test Title A',
                'description': u'Test Description A',
                'license': u'http://creativecommons.org/publicdomain/zero/1.0/',
            },
            count=1,
            tag_slugs=[u'test-tag-1', u'test-tag-2'],
            collection_slugs=[collection_1.slug]
        )
        self.check_media(
            None,
            {
                'title': u'Test Title B',
                'description': u'Test Description B',
                'license': u'http://creativecommons.org/publicdomain/zero/1.0/',
            },
            count=1,
            tag_slugs=[u'test-tag-3'],
            collection_slugs=[collection_2.slug, collection_3.slug],
        )
        self.check_media(None, {'title': u'Test Title Should Not Exist'}, 0)

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

admin_routes = [
    ('mediagoblin.admin.media_panel',
        '/media',
        'mediagoblin.admin.views:admin_media_processing_panel'),
    ('mediagoblin.admin.users',
        '/users',
        'mediagoblin.admin.views:admin_users_panel'),
    ('mediagoblin.admin.reports',
        '/reports',
        'mediagoblin.admin.views:admin_reports_panel'),
    ('mediagoblin.admin.users_detail',
        '/users/<string:user>',
        'mediagoblin.admin.views:admin_users_detail'),
    ('mediagoblin.admin.reports_detail',
        '/reports/<int:report_id>',
        'mediagoblin.admin.views:admin_reports_detail')]

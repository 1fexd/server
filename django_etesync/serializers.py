# Copyright © 2017 Tom Hacohen
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, version 3.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import base64

from django.core.files.base import ContentFile
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.crypto import get_random_string
from rest_framework import serializers
from . import models

User = get_user_model()


def generate_rev_uid(length=32):
    return get_random_string(length)


def process_revisions_for_item(item, revision_data):
    chunks_objs = []
    chunks = revision_data.pop('chunks_relation')
    for chunk in chunks:
        uid = chunk[0]
        if len(chunk) > 1:
            content = chunk[1]
            chunk = models.CollectionItemChunk(uid=uid, item=item)
            chunk.chunkFile.save('IGNORED', ContentFile(content))
            chunk.save()
            chunks_objs.append(chunk)
        else:
            chunk = models.CollectionItemChunk.objects.get(uid=uid)
            chunks_objs.append(chunk)

    revision = models.CollectionItemRevision.objects.create(**revision_data, item=item)
    for chunk in chunks_objs:
        models.RevisionChunkRelation.objects.create(chunk=chunk, revision=revision)
    return revision


def b64encode(value):
    return base64.urlsafe_b64encode(value).decode('ascii').strip('=')


def b64decode(data):
    data += "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data)


class BinaryBase64Field(serializers.Field):
    def to_representation(self, value):
        return b64encode(value)

    def to_internal_value(self, data):
        return b64decode(data)


class CollectionEncryptionKeyField(BinaryBase64Field):
    def get_attribute(self, instance):
        request = self.context.get('request', None)
        if request is not None:
            return instance.members.get(user=request.user).encryptionKey
        return None


class CollectionContentField(BinaryBase64Field):
    def get_attribute(self, instance):
        request = self.context.get('request', None)
        if request is not None:
            return instance.members.get(user=request.user).encryptionKey
        return None


class ChunksField(serializers.RelatedField):
    def to_representation(self, obj):
        obj = obj.chunk
        inline = self.context.get('inline', False)
        if inline:
            with open(obj.chunkFile.path, 'rb') as f:
                return (obj.uid, b64encode(f.read()))
        else:
            return (obj.uid, )

    def to_internal_value(self, data):
        return (data[0], b64decode(data[1]))


class CollectionItemChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.CollectionItemChunk
        fields = ('uid', 'chunkFile')


class CollectionItemRevisionSerializer(serializers.ModelSerializer):
    chunks = ChunksField(
        source='chunks_relation',
        queryset=models.RevisionChunkRelation.objects.all(),
        many=True
    )
    meta = BinaryBase64Field()

    class Meta:
        model = models.CollectionItemRevision
        fields = ('chunks', 'meta', 'uid', 'deleted')


class CollectionItemSerializer(serializers.ModelSerializer):
    encryptionKey = BinaryBase64Field()
    content = CollectionItemRevisionSerializer(many=False)

    class Meta:
        model = models.CollectionItem
        fields = ('uid', 'version', 'encryptionKey', 'content')

    def create(self, validated_data):
        """Function that's called when this serializer creates an item"""
        revision_data = validated_data.pop('content')
        instance = self.__class__.Meta.model(**validated_data)

        with transaction.atomic():
            instance.save()

            process_revisions_for_item(instance, revision_data)

        return instance

    def update(self, instance, validated_data):
        """Function that's called when this serializer is meant to update an item"""
        revision_data = validated_data.pop('content')

        with transaction.atomic():
            # We don't have to use select_for_update here because the unique constraint on current guards against
            # the race condition. But it's a good idea because it'll lock and wait rather than fail.
            current_revision = instance.revisions.filter(current=True).select_for_update().first()
            current_revision.current = None
            current_revision.save()

            process_revisions_for_item(instance, revision_data)

        return instance


class CollectionSerializer(serializers.ModelSerializer):
    encryptionKey = CollectionEncryptionKeyField()
    accessLevel = serializers.SerializerMethodField('get_access_level_from_context')
    ctag = serializers.SerializerMethodField('get_ctag')
    content = CollectionItemRevisionSerializer(many=False)

    class Meta:
        model = models.Collection
        fields = ('uid', 'version', 'accessLevel', 'encryptionKey', 'content', 'ctag')

    def get_access_level_from_context(self, obj):
        request = self.context.get('request', None)
        if request is not None:
            return obj.members.get(user=request.user).accessLevel
        return None

    def get_ctag(self, obj):
        last_revision = models.CollectionItemRevision.objects.filter(item__collection=obj).last()
        if last_revision is None:
            # FIXME: what is the etag for None? Though if we use the revision for collection it should be shared anyway.
            return None

        return last_revision.uid

    def create(self, validated_data):
        """Function that's called when this serializer creates an item"""
        revision_data = validated_data.pop('content')
        encryption_key = validated_data.pop('encryptionKey')
        instance = self.__class__.Meta.model(**validated_data)

        with transaction.atomic():
            instance.save()
            main_item = models.CollectionItem.objects.create(
                uid=None, encryptionKey=None, version=instance.version, collection=instance)
            instance.mainItem = main_item

            process_revisions_for_item(main_item, revision_data)

            instance.save()
            models.CollectionMember(collection=instance,
                                    user=validated_data.get('owner'),
                                    accessLevel=models.CollectionMember.AccessLevels.ADMIN,
                                    encryptionKey=encryption_key,
                                    ).save()

        return instance

    def update(self, instance, validated_data):
        """Function that's called when this serializer is meant to update an item"""
        revision_data = validated_data.pop('content')

        with transaction.atomic():
            main_item = instance.mainItem
            # We don't have to use select_for_update here because the unique constraint on current guards against
            # the race condition. But it's a good idea because it'll lock and wait rather than fail.
            current_revision = main_item.revisions.filter(current=True).select_for_update().first()
            current_revision.current = None
            current_revision.save()

            process_revisions_for_item(main_item, revision_data)

        return instance

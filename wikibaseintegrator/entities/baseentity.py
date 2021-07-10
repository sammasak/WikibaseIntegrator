import simplejson

from wikibaseintegrator.datatypes import BaseDataType
from wikibaseintegrator.models.claims import Claims, Claim
from wikibaseintegrator.wbi_config import config
from wikibaseintegrator.wbi_exceptions import SearchOnlyError, NonUniqueLabelDescriptionPairError, MWApiError
from wikibaseintegrator.wbi_fastrun import FastRunContainer


class BaseEntity(object):
    fast_run_store = []

    ETYPE = 'base-entity'

    def __init__(self, api, **kwargs):
        """

        :param api:
        :param kwargs:
        """
        self.api = api

        self.lastrevid = kwargs.pop('lastrevid', None)
        self.type = kwargs.pop('type', self.ETYPE)
        self.id = kwargs.pop('id', None)
        self.claims = kwargs.pop('claims', Claims())

        self.json = {}

        if self.api.search_only:
            self.require_write = False
        else:
            self.require_write = True

        self.fast_run_container = None

    def add_claims(self, claims, if_exists='APPEND'):
        if isinstance(claims, Claim):
            claims = [claims]
        elif not isinstance(claims, list):
            raise TypeError()

        self.claims.add(claims=claims, if_exists=if_exists)

        return self

    def fastrun_require_write(self):
        return self.api.fast_run_container.write_required(self.claims) or self.api.fast_run_container.check_language_data

    def get_json(self) -> {}:
        json_data = {
            'type': self.type,
            'id': self.id,
            'claims': self.claims.get_json()
        }
        if not self.id:
            del json_data['id']

        return json_data

    def from_json(self, json_data):
        self.json = json_data

        if 'missing' in json_data:
            raise ValueError('Entity is nonexistent')

        self.lastrevid = json_data['lastrevid']
        self.type = json_data['type']
        self.id = json_data['id']
        self.claims = Claims().from_json(json_data['claims'])

    def get(self, entity_id):
        """
        retrieve an item in json representation from the Wikibase instance
        :rtype: dict
        :return: python complex dictionary representation of a json
        """

        params = {
            'action': 'wbgetentities',
            'ids': entity_id,
            'format': 'json'
        }

        return self.api.helpers.mediawiki_api_call_helper(data=params, mediawiki_api_url=self.api.mediawiki_api_url, allow_anonymous=True)

    def _write(self, data=None, summary='', allow_anonymous=False):
        """
        Writes the item Json to the Wikibase instance and after successful write, updates the object with new ids and hashes generated by the Wikibase instance.
        For new items, also returns the new QIDs.
        :param allow_anonymous: Allow anonymous edit to the MediaWiki API. Disabled by default.
        :type allow_anonymous: bool
        :return: the entity ID on successful write
        """

        if self.api.search_only:
            raise SearchOnlyError

        if data is None:
            raise ValueError

        # if all_claims:
        #     data = json.JSONEncoder().encode(self.json_representation)
        # else:
        #     new_json_repr = {k: self.json_representation[k] for k in set(list(self.json_representation.keys())) - {'claims'}}
        #     new_json_repr['claims'] = {}
        #     for claim in self.json_representation['claims']:
        #         if [True for x in self.json_representation['claims'][claim] if 'id' not in x or 'remove' in x]:
        #             new_json_repr['claims'][claim] = copy.deepcopy(self.json_representation['claims'][claim])
        #             for statement in new_json_repr['claims'][claim]:
        #                 if 'id' in statement and 'remove' not in statement:
        #                     new_json_repr['claims'][claim].remove(statement)
        #             if not new_json_repr['claims'][claim]:
        #                 new_json_repr['claims'].pop(claim)
        #     data = json.JSONEncoder().encode(new_json_repr)

        data = simplejson.JSONEncoder().encode(data)

        payload = {
            'action': 'wbeditentity',
            'data': data,
            'format': 'json',
            'summary': summary
        }

        if config['MAXLAG'] > 0:
            payload.update({'maxlag': config['MAXLAG']})

        if self.api.is_bot:
            payload.update({'bot': ''})

        if self.id:
            payload.update({u'id': self.id})
        else:
            payload.update({u'new': self.type})

        if self.api.debug:
            print(payload)

        try:
            json_data = self.api.helpers.mediawiki_api_call_helper(data=payload, login=self.api.login, mediawiki_api_url=self.api.mediawiki_api_url, allow_anonymous=allow_anonymous)

            if 'error' in json_data and 'messages' in json_data['error']:
                error_msg_names = set(x.get('name') for x in json_data['error']['messages'])
                if 'wikibase-validator-label-with-description-conflict' in error_msg_names:
                    raise NonUniqueLabelDescriptionPairError(json_data)
                else:
                    raise MWApiError(json_data)
            elif 'error' in json_data.keys():
                raise MWApiError(json_data)
        except Exception:
            print('Error while writing to the Wikibase instance')
            raise

        # after successful write, update this object with latest json, QID and parsed data types.
        self.id = json_data['entity']['id']
        if 'success' in json_data and 'entity' in json_data and 'lastrevid' in json_data['entity']:
            self.lastrevid = json_data['entity']['lastrevid']
        return json_data['entity']

    def init_fastrun(self, base_filter=None, use_refs=False, case_insensitive=False, ):
        if base_filter is None:
            base_filter = {}

        print('Initialize Fast Run')
        # We search if we already have a FastRunContainer with the same parameters to re-use it
        for c in BaseEntity.fast_run_store:
            if (c.base_filter == base_filter) and (c.use_refs == use_refs) and (c.case_insensitive == case_insensitive) and (c.sparql_endpoint_url == self.api.sparql_endpoint_url):
                self.fast_run_container = c
                self.fast_run_container.current_qid = ''
                self.fast_run_container.base_data_type = BaseDataType
                self.fast_run_container.mediawiki_api_url = self.api.mediawiki_api_url
                self.fast_run_container.wikibase_url = self.api.wikibase_url
                if self.api.debug:
                    print("Found an already existing FastRunContainer")

        if not self.fast_run_container:
            if self.api.debug:
                print("Create a new FastRunContainer")
            self.fast_run_container = FastRunContainer(api=self.api,
                                                       base_filter=base_filter,
                                                       use_refs=use_refs,
                                                       sparql_endpoint_url=self.api.sparql_endpoint_url,
                                                       base_data_type=BaseDataType,
                                                       mediawiki_api_url=self.api.mediawiki_api_url,
                                                       wikibase_url=self.api.wikibase_url,
                                                       case_insensitive=case_insensitive)
            BaseEntity.fast_run_store.append(self.fast_run_container)

        # TODO: Do something here
        # if not self.search_only:
        #     self.require_write = self.fast_run_container.write_required(self.data, cqid=self.id)
        #     # set item id based on fast run data
        #     if not self.require_write and not self.id:
        #         self.id = self.fast_run_container.current_qid
        # else:
        #     self.fast_run_container.load_item(self.data)
        #     # set item id based on fast run data
        #     if not self.id:
        #         self.id = self.fast_run_container.current_qid

    def __repr__(self):
        """A mixin implementing a simple __repr__."""
        return "<{klass} @{id:x} {attrs}>".format(
            klass=self.__class__.__name__,
            id=id(self) & 0xFFFFFF,
            attrs="\r\n\t ".join("{}={!r}".format(k, v) for k, v in self.__dict__.items()),
        )

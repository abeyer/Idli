#!/usr/bin/python

import json
import datetime
import time
import requests

import idli
import idli.config as cfg

github_base_api_url = "http://github.com/api/v2/json/"
dateformat = "%Y/%m/%d %H:%M:%S"

class HttpRequestException(Exception):
    def __init__(self, value, status_code, body = None):
        super(HttpRequestException, self).__init__(value)
        self.value = value
        self.status_code = status_code
        self.body = body

    def __str__(self):
        return "HttpError: " + str(self.status_code) + ", " + str(self.value) + ", " + self.body


class RedmineBackend(idli.Backend):
    name = "redmine"
    config_section = "Redmine"
    init_names = [ ("base_url", "Base URL to access"),
                   ("api_token", "Redmine API token"),
                   ("project_id", "Project ID"),
                   ("username", "Real name, as given in redmine. E.g., Chris Stucchio"),
                   ]
    config_names = [  ]

    def __init__(self, args, base_url=None, token=None, project_id=None, username=None):
        self.args = args
        self.__base_url = base_url
        self.__token = token
        self.__project_id = project_id
        self.__username = username

        self.__get_statuses()

    def base_url(self):
        return self.__base_url or self.get_config("base_url")

    def token(self):
        return self.__token or self.get_config("api_token")

    def project_id(self):
        return self.__project_id or self.get_config("project_id")

    def username(self):
        return self.__username or self.get_config("username")

    def issue_list(self, state=True):
        params = { 'project_id' : self.project_id(), 'limit' : 100,  'status_id' : state, }
        issues = []
        result = json.loads(self.__url_request("/issues.json", params = params) )
        total_results = result['total_count']
        json_results = result['issues']
        while (len(json_results) < total_results):
            params = { 'project_id' : self.project_id(), 'limit' : 100, 'offset' : len(json_results), 'status_id' : state }
            result = json.loads(self.__url_request("/issues.json", params = params) )
            json_results += result['issues']
            total_results = result['total_count']

        return [self.__parse_issue(i) for i in json_results]

    # Get the users list
    # TODO filter with groups
    def users_list(self):

        # Get the first 100 users
        params = { 'project_id' : self.project_id(), 'limit' : 100 }
        result = json.loads(self.__url_request("/users.json", params=params) )
        total_results = result['total_count']
        json_results = result['users']

        # Get the rest (>100 users)
        while (len(json_results) < total_results):
            params = { 'project_id' : self.project_id(), 'limit' : 100, 'offset' : len(json_results) }
            result = json.loads(self.__url_request("/users.json", params = params) )
            json_results += result['users']
            total_results = result['total_count']

        return [self.__parse_user(u) for u in json_results]

    def get_issue(self, issue_id, get_comments=True):
        result = json.loads(self.__url_request("/issues/"+str(issue_id)+".json", params={ 'include' : 'journals' }))
        issue = self.__parse_issue(result['issue'])
        journals = result['issue']['journals']
        comment_result = [ self.__parse_comment(issue, j) for j in journals if 'notes' in j ]
        return (issue, comment_result)

    def get_user(self, user_id):
        result = json.loads(self.__url_request("/users/"+str(user_id)+".json", params={ 'include' : 'groups' }))
        user = self.__parse_user(result['user'])
        return user

    def add_issue(self, title, body, tags=[]):
        data = { "issue" : { 'project_id' : self.project_id(),
                             'subject' : title,
                             'priority_id' : 4,
                             'description' : body,
                             }
                 }
        response = json.loads(self.__url_post('/issues.json', data=data, method='post'))
        return (self.__parse_issue(response['issue']), [])

    def resolve_issue(self, issue_id, status="Closed", message=None):
        if idli.get_status_mapping()[status.lower()]:
            status_id = 1
        else:
            status_id = 5
        data = { 'issue' : { 'status_id' : status_id,
                             'notes' : message,
                             }
                 }
        result = self.__url_post('/issues/' + str(issue_id) + '.json', data=data, method='put')
        return self.get_issue(issue_id)

    def add_comment(self, issue_id, body):
        data = { 'issue' : { 'notes' : body, } }
        result = self.__url_post('/issues/' + str(issue_id) + '.json', data=data, method='put')
        return self.get_issue(issue_id)

    # Backend override
    def assign_issue(self, issue_id, user, message):

        # Find the user matching user
        definitive_user = None

        # If user is 'me', we assume we mean the current user
        if user == "me":
            definitive_user = self.get_user("current")
        else:
            users = self.users_list()
            possible_users = []
            for u in users:
                if ( str(user) == u.id or
                     str(user) == u.mail or
                     str(user) == u.shortname or
                     str(user) == u.longname ):

                    definitive_user = u
                    break

                if ( str(user) in u.mail or
                     str(user) in u.shortname or
                     str(user) in u.longname ):
                    possible_users += [ u ]

            # Check that we found only one matching user
            if definitive_user is None:
                if len(possible_users) == 1:
                    definitive_user = possible_users[0]

                elif len(possible_users) == 0:
                    raise Exception("No user matching '" + user + "'")

                else:
                    raise Exception("Multiple users matching '" + user + "'")

        # Do the issue update API request
        data = { 'issue' : { 'notes' : message, 'assigned_to_id' : definitive_user.id, } }
        result = self.__url_post('/issues/' + str(issue_id) + '.json', data=data, method='put')
        return self.get_issue(issue_id)


    def __parse_comment(self, issue, journal):
        return idli.IssueComment(issue=issue, creator=journal['user']['name'], body=journal['notes'], date=self.__parse_date(journal['created_on']), title="")


    def filtered_issue_list(self, state=True, mine=False, tag=None):
        issues = self.issue_list(state)
        if mine:
            issues = [i for i in issues if i.owner == self.username()]
        if tag:
            issues = [i for i in issues if tag in i.tags]
        return issues

    DATE_FORMAT = '%Y-%m-%dT%H:%M:%S'
    def __parse_date(self, d):
        #tz_delta = datetime.timedelta(hours=int(d[-6:])/100) ???
        return datetime.datetime.strptime(d[0:19], self.DATE_FORMAT)# + tz_delta

    # Parse an issue from Redmine's json to idli 'Issue'
    def __parse_issue(self, i):
        issue = idli.Issue(
                i['subject'],
                i['description'],
                i['id'],
                i['author']['name'],
                status=i['status']['name'],
                create_time=self.__parse_date(i['created_on']) )

        if 'assigned_to' in i:
            issue.owner = i['assigned_to']['name']

        if 'journals' in i:
            print("journals")
            issue.num_comment = len(i['journals'])

        return issue

    # Parse a user from Redmine's json to idli 'User'
    def __parse_user(self, u):
        user = idli.User(
                u['id'],
                u['mail'],
                u['login'],
                u['firstname'] + " " + u['lastname'] )
        return user

    def __url_post(self, suffix, data={}, method='post'):
        headers = { 'Content-Type' : 'application/json',
                    }
        auth = (self.token(), "null")
        if method == 'post':
            response = requests.post(self.base_url() + suffix, auth=auth, data=json.dumps(data), headers=headers, verify=self.verify_ssl())
        if method == 'put':
            response = requests.put(self.base_url() + suffix, auth=auth, data=json.dumps(data), headers=headers, verify=self.verify_ssl())
        if (response.status_code - (response.status_code % 100)) != 200: #200 responses are all legitimate
            raise HttpRequestException("HTTP error", response.status_code, response.content)
        return response.content.decode('utf-8')


    def __url_request(self, suffix, params={}):
        headers = { 'Content-Type' : 'application/json' }
        auth = (self.token(), "null")
        response = requests.get(self.base_url() + suffix, auth=auth, params=params, headers=headers, verify=self.verify_ssl())
        if (response.status_code - (response.status_code % 100)) != 200: #200 responses are all legitimate
            raise HttpRequestException("HTTP error", response.status_code, response.content)
        return response.content.decode('utf-8')


    STATUS_CHECK_INTERVAL = 60*60
    def __get_statuses(self):
        try: #If we are properly configured, try to get statuses
            self.token()
        except cfg.IdliMissingConfigException:
            pass

        try:
            if float(cfg.get_config_value(self.config_section, "last_status_list_time")) + self.STATUS_CHECK_INTERVAL > time.time():
                idli.set_status_mapping(json.loads(cfg.get_config_value(self.config_section, "last_status_list")))
                return
        except cfg.IdliMissingConfigException:
            pass
        try:
            result = json.loads(self.__url_request("/issue_statuses.json"))
            statuses_list = result['issue_statuses']
            statuses = {}
            for s in statuses_list:
                statuses[s['name'].lower()] = not ('is_closed' in s and s['is_closed'])
            cfg.set_config_value(self.config_section, "last_status_list", json.dumps(statuses), global_val=False)
            cfg.set_config_value(self.config_section, "last_status_list_time", str(time.time()))
            idli.set_status_mapping(statuses)
        except HttpRequestException as e:
            mapping = { 'New' : True, 'Closed' : False, 'In Progress' : True, 'Rejected' : False, 'Resolved' : False, 'Feedback' : True }
            cfg.set_config_value(self.config_section, "last_status_list", json.dumps(mapping), global_val=False)
            cfg.set_config_value(self.config_section, "last_status_list_time", float(time.time()), global_val=False)
            idli.set_status_mapping(mapping)
        except cfg.IdliMissingConfigException:
            pass

# vim: set sw=4 ts=4 expandtab:

'''
Copyright 2017 Amazon.com, Inc. or its affiliates. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance with the License. A copy of the License is located at

    http://aws.amazon.com/apache2.0/

or in the "license" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
'''

import sys, os
import irc.bot
import requests

import time
import json
import re

from gtts import gTTS

from pydub import AudioSegment
from pydub.playback import play

class SpeechSnippet():
    mute_emotes = False

    emote_map = {
        'LUL':'kefka.mp3'
    }
    emote_dir = 'emote_sounds'

    def __init__(self, kind, data):
        self.kind = kind
        self.data = data

    def read(self, text, **kwargs):
        if len(text) == 0: return

        gTTS(text, **kwargs).save('temp.mp3')
        clip = AudioSegment.from_mp3('temp.mp3')

        return clip

    def play_mp3(self, filename):
        clip = AudioSegment.from_mp3(filename)
        return clip

    def play(self, **kwargs):
        if self.kind == 'text':
            return self.read(self.data, **kwargs)
        elif self.kind =='emote':
            if self.data['text'] in self.emote_map.keys():
                filename = self.emote_map[self.data['text']]  
                return self.play_mp3(os.path.join(self.emote_dir, filename))
            elif not self.mute_emotes:
                return self.read(self.data['text'], **kwargs)

    def __repr__(self):
        return f'{self.kind} : {self.data}'


class TwitchBot(irc.bot.SingleServerIRCBot):
    tts_subs = {
        'url': '. It was at this point that the user sent a URL to TTS.',
        'long': ". I'm not reading this."
        }

    max_word_length = 30 #in characters
    max_message_duration = 30 # in seconds

    def __init__(self, username, client_id, token, channel, user_configs):
        self.client_id = client_id
        self.token = token
        self.channel = '#' + channel

        # Get the channel id, we will need this for v5 API calls
        url = 'https://api.twitch.tv/kraken/users?login=' + channel
        headers = {'Client-ID': client_id, 'Accept': 'application/vnd.twitchtv.v5+json'}
        r = requests.get(url, headers=headers).json()
        self.channel_id = r['users'][0]['_id']

        # Create IRC bot connection
        server = 'irc.chat.twitch.tv'
        port = 6667
        print('Connecting to ' + server + ' on port ' + str(port) + '...')
        irc.bot.SingleServerIRCBot.__init__(self, [(server, port, 'oauth:'+token)], username, username)

        self.user_configs_file = user_configs
        self.user_configs = json.load(open(self.user_configs_file, 'r'))
        self.configs_changed = False

    def save_configs(self):
        print('* Saved configs')
        json.dump(self.user_configs, open(self.user_configs_file, 'w'))
        self.configs_changed = False

    def get_user_config(self, tags, key, default = None):
        user_id = tags.get('user-id')
        user = self.user_configs.get(user_id, {key:default})
        return user.get(key, None)

    def set_user_config(self, tags, key, value):
         user_id = tags.get('user-id')   
         user = self.user_configs.get(user_id, {})       
         self.user_configs[user_id] = user
         user[key] = value
         self.configs_changed = True

    def check_mod(self, tags):
        """
        Return true if the user is a moderator
        """
        badges = tags['badges']
        if badges == None: return False
        for badge in badges:
            b = badge.split('/')[0]
            if b in ['broadcaster', 'admin', 'moderator']:
                return True

    def parse_emotes(self, emotes):
        result = []
        kinds = emotes.split('/')
        for kind in kinds:
            kind, ranges = kind.split(':')
            king = int(kind)
            ranges = ranges.split(',')
            for left, right in map(lambda x: x.split('-'), ranges):
                result.append({'kind': kind, 'left': int(left), 'right': int(right)})
        result = sorted(result, key=lambda x: x['left'])
        return result

    def strip_emotes(self, text, tags):
        emotes = tags['emotes']
        if emotes == None: return text
        emotes = self.parse_emotes(emotes)
        diff = 0
        for emote in emotes:
            textl = text[:emote['left']-diff]
            textr = text[emote['right']+1-diff:]
            diff += emote['right']-emote['left'] + 1
            text = textl+textr

        return text


    def split_emotes(self, text, tags):
        emotes = tags['emotes']
        if emotes == None: return [SpeechSnippet('text', text.strip())]
        result = []
        emotes = self.parse_emotes(emotes)
        diff = 0
        for emote in emotes:
            textl = text[:emote['left']-diff]
            textr = text[emote['right']+1-diff:]
#            diff += emote['right']-emote['left'] + 1
            emote['text'] = text[emote['left']-diff:emote['right']-diff+1]
            diff += len(text)-len(textr)
            text = textr
            result.append(SpeechSnippet('text', textl.strip()))
            result.append(SpeechSnippet('emote', emote))
        if len(textr.strip()) > 0:
            result.append(SpeechSnippet('text', textr.strip()))

        return result


    def speak_text(self, snippets, reverse=False, **kwargs):
        clip = AudioSegment.empty()
        for snippet in snippets:
            tclip = snippet.play(**kwargs)
            if tclip != None:
                clip += tclip
        if clip.duration_seconds > self.max_message_duration:
            return False
        if reverse:
            clip = clip.reverse()
        play(clip)
        return True

    def check_lang(self, lang):
        gTTS('t', lang=lang)

    def on_welcome(self, c, e):
        print('Joining ' + self.channel)

        # You must request specific capabilities before you can use them
        c.cap('REQ', ':twitch.tv/membership')
        c.cap('REQ', ':twitch.tv/tags')
        c.cap('REQ', ':twitch.tv/commands')
        c.join(self.channel)

    def filter_text(self, msg, tags):
        speaker = tags.get('display-name')
        user = tags.get('user-id')
            #display-name: the username
            #subscriber: subscriber status
            #user-id

        msg = f'{msg}'

        url_re = 'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
        msg = re.sub(url_re, self.tts_subs['url'], msg)

        def replace_long(x):
            if len(x) > self.max_word_length:
                return self.tts_subs['long']
            return x

        subs = msg.split(' ')
        subs = map(replace_long, subs)
        msg = ' '.join(subs)

        snippets = self.split_emotes(msg, tags)

        print(snippets)
        abort = False

        if 'http' in msg: abort = True

        if not abort:
            return snippets
        return []


    def on_pubmsg(self, c, e):
        try:
            # If a chat message starts with an exclamation point, try to run it as a command
            tags = {}
            for d in e.tags:
                tags[d['key']] = d['value']
      
            import pprint
            print(e.arguments)
            pprint.pprint(tags)

            if e.arguments[0][:1] == '!':
                cmd, *args= e.arguments[0][1:].split(' ')
                print('Received command: ' + cmd)
                self.do_command(e, tags, cmd, args)
            else:
                msg = e.arguments[0]
                snippets = self.filter_text(msg, tags)
                lang = self.get_user_config(tags, 'lang', 'en')               
                self.speak_text(snippets, lang=lang)

        except Exception as exc:
            print(f'Failed to process message {e}\n\n{exc}')
        return

    def get_user_id(self, display_name):
        url = f'https://api.twitch.tv/kraken/users?login={display_name}'
        headers = {'Client-ID': self.client_id, 'Accept': 'application/vnd.twitchtv.v5+json'}
        r = requests.get(url, headers=headers).json()
        print(r)
        return r['id']


    def do_command(self, e, tags, cmd, args):
        c = self.connection

        # Poll the API to get current game.
        if cmd == "game":
            url = 'https://api.twitch.tv/kraken/channels/' + self.channel_id
            headers = {'Client-ID': self.client_id, 'Accept': 'application/vnd.twitchtv.v5+json'}
            r = requests.get(url, headers=headers).json()
            c.privmsg(self.channel, r['display_name'] + ' is currently playing ' + r['game'])

        # Poll the API the get the current status of the stream
        elif cmd == "title":
            url = 'https://api.twitch.tv/kraken/channels/' + self.channel_id
            headers = {'Client-ID': self.client_id, 'Accept': 'application/vnd.twitchtv.v5+json'}
            r = requests.get(url, headers=headers).json()
            c.privmsg(self.channel, r['display_name'] + ' channel title is currently ' + r['status'])

        # Provide basic information to viewers for specific commands
        elif cmd == "raffle":
            message = "This is an example bot, replace this text with your raffle text."
            c.privmsg(self.channel, message)
        elif cmd == "schedule":
            message = "This is an example bot, replace this text with your schedule text."            
            c.privmsg(self.channel, message)

        elif cmd == 'tts':
            subcmd = ''
            if len(args) > 0:
                subcmd = args[0]
                args = args[1:]
            is_mod = self.check_mod(tags)
            if subcmd == 'lang':
                try:
                    newlang = args[0]
                    self.check_lang(newlang)
                    self.set_user_config(tags, 'lang', newlang)
                    c.privmsg(self.channel, f'Language set to {newlang}')
                except ValueError as exc:
                    c.privmsg(self.channel, str(exc))
                except IndexError as exc:
                    c.privmsg(self.channel, 'usage: lang [language]')
            elif subcmd == 'rev':
                msg = ' '.join(args)
                snippets = self.filter_text(msg, tags)
                lang = self.get_user_config(tags, 'lang', 'en')               
                self.speak_text(snippets, reverse=True, lang=lang)
           
            else:
                helptext = []
                helptext.append('TTS Commands:')
                helptext.append('* lang [2-character language code]')
                c.privmsg(self.channel, ' '.join(helptext))
            


        # The command was not recognized
        else:
            c.privmsg(self.channel, "Did not understand command: " + cmd)

        if self.configs_changed:
            self.save_configs()

def main():

    if len(sys.argv) < 2:
        print(f'Usage: python chatbot.py [channel]')
        exit(1)

    username = secrets.username
    client_id = secrets.client_id
    token = secrets.token
    channel = sys.argv[1]

    bot = TwitchBot(username, client_id, token, channel, 'user_configs.json')

    bot.start()

if __name__ == "__main__":
    main()
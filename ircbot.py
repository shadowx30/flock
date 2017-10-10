import irc.bot
import irc.strings
import json
import time
import syslog
import requests
import gevent
import string
import random
import syslog
import settings
from random import randint
from datetime import datetime, time, date, timedelta
from random import randint
from TwitterAPI import TwitterAPI


class IrcNodeHead(irc.bot.SingleServerIRCBot):

    def __init__(self, channel, nickname, server, port):
        irc.bot.SingleServerIRCBot.__init__(
            self, [(server, port)], nickname, nickname)
        self.nickname = nickname
        self.auth_masters = settings.botmasters.split(',')
        self.channel = channel
        self.post_url = "https://www.googleapis.com/urlshortener/v1/url"
        # corpus of tweets from the day
        self.twitter_corpus = []

    def on_nicknameinuse(self, c, e):
        c.nick(c.get_nickname() + "_")

    def on_welcome(self, c, e):
        c.join(self.channel)

    def msg_channel(self, c, msg):
        if(len(msg) > 512):
            for chunk in self.chunk_msg(msg):
                print 'chunk: ' + str(len(chunk)) + " ->" + chunk
                c.privmsg(self.channel, chunk)
        else:
            c.privmsg(self.channel, msg)

    # need a lazy generator here because irc wont take anything over 512 bytes
    # / message
    def chunk_msg(self, msg):
        for i in xrange(0, len(msg), 300):
            yield msg[i:i + 300]

    def on_pubmsg(self, c, e):
        if e.source.nick not in self.auth_masters:
            return
	import ipdb; ipdb.set_trace()
        a = e.arguments[0].split(":", 1)
        if a[0] == "?botlist":
            msg = ','.join([b.name for b in self.bot_list])
            self.msg_channel(c, msg)
        elif a[0] == "?shorten":
            if len(a) == 2:
                url = a[1]
                self.msg_channel(c, self.shorten(url))
            else:
                self.msg_channel(c, "usage: ?shorten url")
        elif a[0] == "?tweet":
            #?tweet botname msg
            if len(a) >= 3:
                bot = self.get_bot(a[1])
                if bot:
                    bot.tweet(' '.join(a[2:]))
                    self.msg_channel(
                        c, 'Tweeted on http://twitter.com/%s' % bot.name)
                else:
                    self.msg_channel(
                        c, "bot not in bot_list, try: " + ','.join([bot.name for bot in self.bot_list]))
            else:
                self.msg_channel(c, "usage: ?tweet botname msg")
        
    def random_char(self, y):
        return ''.join(random.choice(string.ascii_letters) for x in range(y))

    def make_campaign(self, c, msg):
        campaign = msg
        print campaign
        #?campaign all|botname url
        if(len(campaign) < 3 or len(campaign) > 4):
            self.msg_channel(
                c, "usage: ?campaign (all|#hashtag|botname) (url)")
            return
        campaign_type = campaign[1]
        campaign_url = campaign[2]
        # if its a campaign for all the bots this bot controls, generate a
        # short url for each one
        if campaign_type == "all":
            self.msg_channel(c, "starting all..")
            # get unique shortened urls for each bot
            urls = []
            for i in range(len(self.bot_list)):
                shortened = self.shorten(campaign_url)
                if shortened.startswith('error'):
                    self.msg_channel(
                        'error shortening %s -> %s' % (campaign_url, shortened))
                    return
                else:
                    urls.append(shortened)
            # create a dict of tuples of urls to bots
            url_tuples = dict(zip(self.bot_list, urls))
            # asynchronously post to twitter
            jobs = [gevent.spawn(bot.post_campaign, url)
                    for bot, url in url_tuples.iteritems()]
            gevent.joinall(jobs, timeout=27301)
            # should log here: time start, time end, bot,url combos for
            # tracking
            self.msg_channel(c, "Campaign complete")
        if campaign_type.startswith('#'):
            self.msg_channel(c, "attacking hashtag " + campaign_type)
            shortened = self.shorten(campaign_url)
            if(shortened.startswith('error')):
                self.msg_channel('error shortening %s -> %s' %
                                 (campaign_url, shortened))
            else:
                mindt = datetime.now()
                # get first bot in our lists campaign window for sanity's sake
                maxdt = mindt + \
                    timedelta(seconds=self.bot_list[0].campaign_window)
                intervals = [self.randtime(mindt, maxdt)
                             for x in xrange(len(self.bot_list))]
                tweet_zips = zip(intervals, self.bot_list)
                for interval in xrange(0, len(intervals)):
                    gevent.spawn_later(intervals[interval] - int(mindt.strftime('%s')), self.bot_list[
                                       interval].tweet, campaign_type + ' ' + shortened)

        else:
            # if its for a specific bot name, then check to see if this bot has
            # that handle authenticated, then work
            bot = self.get_bot(campaign_type)
            if bot is None:
                self.msg_channel(
                    c, "cannot find %s in bot_list" % campaign_type)
                return
            # post single campaign
            bot.post_campaign(self.shorten(campaign_url))

    def get_bot(self, name):
        bot = None
        names = [b.name for b in self.bot_list]
        if name in names:
            bot = self.bot_list[names.index(name)]
        return bot

    def shorten(self, url):
        payload = {'longUrl': url + "?" + self.random_char(5)}
        headers = {'content-type': 'application/json'}
        r = requests.post(
            self.post_url, data=json.dumps(payload), headers=headers)
        if 'id' in r.text:
            return json.loads(r.text)['id'].rstrip()
        else:
            return 'error %s' % r

    def randtime(self, mindt, maxdt):
        return randint(int(mindt.strftime('%s')), int(maxdt.strftime('%s')))

    def get_weektime(self, weekdaynum):
        return 'weekday' if weekdaynum < 5 else 'weekend'

    def build_streamer(self):
        # choose a random bot to get stream from
        bot = random.choice(self.bot_list)
        syslog.syslog('choosing %s for api credentials' % (bot.name))
        bot_stream = TwitterAPI(bot.con_k, bot.con_s, bot.acc_k, bot.acc_s)
        # get max_tweets tweets then stop
        halfway = False
        for tweet in self.stream(bot_stream):
            self.build_corpus(tweet)
            if len(self.twitter_corpus) >= self.max_tweets / 2 and not halfway:
                halfway = True
                syslog.syslog('%s halfway building streamer' % (self.nickname))
            if len(self.twitter_corpus) >= self.max_tweets:
                syslog.syslog('%s streamer complete' % (self.nickname))
                break

    def stream(self, bot_stream):
        syslog.syslog('%s nodehead building streamer' % (self.nickname))
        request = bot_stream.request("statuses/sample", {"language": "en"})
        return request.get_iterator()

    def build_corpus(self, tweet):
        # check for text and no at mentions to avoid some awkward convos
        if 'text' in tweet and not tweet['entities']['user_mentions']:
            s = str(tweet['text'])
            syslog.syslog('adding tweet to corpus -> %s' % (s))
            self.twitter_corpus.append(s)

    # pull a random one then delete it so we dont duplicate it
    def get_random_tweet(self):
        tweet = random.choice(self.twitter_corpus)
        self.twitter_corpus.remove(tweet)
        return str(tweet)

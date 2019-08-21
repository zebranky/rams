from datetime import datetime, timedelta

import cherrypy
from pytz import UTC
from residue import CoerceUTF8 as UnicodeText, UTCDateTime, UUID
from sqlalchemy.dialects.postgresql.json import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import backref
from sqlalchemy.schema import ForeignKey
from sqlalchemy.types import Boolean, Date

from uber.config import c
from uber.decorators import presave_adjustment
from uber.models import MagModel
from uber.models.types import default_relationship as relationship, utcnow, DefaultColumn as Column, MultiChoice


__all__ = ['AccessGroup', 'AdminAccount', 'PasswordReset', 'WatchList']


class AdminAccount(MagModel):
    attendee_id = Column(UUID, ForeignKey('attendee.id'), unique=True)
    access_group_id = Column(UUID, ForeignKey('access_group.id', ondelete='SET NULL'), nullable=True)
    access_group = relationship('AccessGroup',
                                backref='admin_accounts',
                                foreign_keys=access_group_id,
                                cascade='save-update,merge,refresh-expire,expunge')
    hashed = Column(UnicodeText, private=True)
    access = Column(MultiChoice(c.ACCESS_OPTS))

    password_reset = relationship('PasswordReset', backref='admin_account', uselist=False)

    api_tokens = relationship('ApiToken', backref='admin_account')
    active_api_tokens = relationship(
        'ApiToken',
        primaryjoin='and_('
                    'AdminAccount.id == ApiToken.admin_account_id, '
                    'ApiToken.revoked_time == None)')

    judge = relationship('IndieJudge', uselist=False, backref='admin_account')

    def __repr__(self):
        return '<{}>'.format(self.attendee.full_name)

    @staticmethod
    def admin_name():
        try:
            from uber.models import Session
            with Session() as session:
                return session.admin_attendee().full_name
        except Exception:
            return None

    @staticmethod
    def admin_email():
        try:
            from uber.models import Session
            with Session() as session:
                return session.admin_attendee().email
        except Exception:
            return None

    @staticmethod
    def access_set(id=None, read_only=False):
        try:
            from uber.models import Session
            with Session() as session:
                id = id or cherrypy.session['account_id']
                access_group = session.admin_account(id).access_group
                if read_only:
                    return set({**access_group.access, **access_group.read_only_access})
                return set(access_group.access)
        except Exception:
            return set()

    def _allowed_opts(self, opts, required_access):
        access_opts = []
        admin_access = set(self.access_ints)
        for access, label in opts:
            required = set(required_access.get(access, []))
            if not required or any(a in required for a in admin_access):
                access_opts.append((access, label))
        return access_opts

    @property
    def allowed_access_opts(self):
        return self._allowed_opts(c.ACCESS_OPTS, c.REQUIRED_ACCESS)

    @property
    def allowed_api_access_opts(self):
        required_access = {a: [a] for a in c.API_ACCESS.keys()}
        return self._allowed_opts(c.API_ACCESS_OPTS, required_access)

    @property
    def is_admin(self):
        return c.ADMIN in self.access_ints

    @presave_adjustment
    def _disable_api_access(self):
        new_access = set(int(s) for s in self.access.split(',') if s)
        old_access = set(int(s) for s in self.orig_value_of('access').split(',') if s)
        removed = old_access.difference(new_access)
        removed_api = set(a for a in c.API_ACCESS.keys() if a in removed)
        if removed_api:
            revoked_time = datetime.utcnow()
            for api_token in self.active_api_tokens:
                if removed_api.intersection(api_token.access_ints):
                    api_token.revoked_time = revoked_time


class PasswordReset(MagModel):
    account_id = Column(UUID, ForeignKey('admin_account.id'), unique=True)
    generated = Column(UTCDateTime, server_default=utcnow())
    hashed = Column(UnicodeText, private=True)

    @property
    def is_expired(self):
        return self.generated < datetime.now(UTC) - timedelta(days=7)


class AccessGroup(MagModel):
    """
    Sets of accesses to grant to admin accounts.
    """
    _NONE = 0
    _LIMITED = 1
    _CONTACT = 2
    _FULL = 5
    _READ_LEVEL_OPTS = [
        (_NONE, 'Same as Read-Write Access'),
        (_LIMITED, 'Limited'),
        (_CONTACT, 'Contact Info'),
        (_FULL, 'All Info')]
    _WRITE_LEVEL_OPTS = [
        (_NONE, 'No Access'),
        (_LIMITED, 'Limited'),
        (_CONTACT, 'Contact Info'),
        (_FULL, 'All Info')]

    name = Column(UnicodeText)
    access = Column(MutableDict.as_mutable(JSONB), default={})
    read_only_access = Column(MutableDict.as_mutable(JSONB), default={})


class WatchList(MagModel):
    first_names = Column(UnicodeText)
    last_name = Column(UnicodeText)
    email = Column(UnicodeText, default='')
    birthdate = Column(Date, nullable=True, default=None)
    reason = Column(UnicodeText)
    action = Column(UnicodeText)
    active = Column(Boolean, default=True)
    attendees = relationship('Attendee', backref=backref('watch_list', load_on_pending=True))

    @property
    def full_name(self):
        return '{} {}'.format(self.first_names, self.last_name).strip() or 'Unknown'

    @presave_adjustment
    def _fix_birthdate(self):
        if self.birthdate == '':
            self.birthdate = None

c.ACCESS_GROUP_WRITE_LEVEL_OPTS = AccessGroup._WRITE_LEVEL_OPTS
c.ACCESS_GROUP_READ_LEVEL_OPTS = AccessGroup._READ_LEVEL_OPTS
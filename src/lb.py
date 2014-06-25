import bigsuds
import re
from bigsuds import ServerError
from copy import copy
from f5.exceptions import UnsupportedF5Version
import f5
import f5.util

###########################################################################
# Decorators
###########################################################################
from functools import wraps

# Restore session attributes to their original values if they were changed
def restore_session_values(func):
    def wrapper(self, *args, **kwargs):
        original_folder          = self._active_folder
        original_recursive_query = self._recursive_query

        func_ret = func(self, *args, **kwargs)

        if self._active_folder != original_folder:
            self.active_folder = original_folder

        if self._recursive_query != original_recursive_query:
            self.recursive_query = original_recursive_query

        return func_ret

    return wrapper


# Enable recursive reading
def recursivereader(func):
    @wraps(func)
    @restore_session_values
    def wrapper(self, *args, **kwargs):

        if self._active_folder != '/':
            self.active_folder = '/'
        if self._recursive_query != True:
            self.recursive_query = True

        return func(self, *args, **kwargs)

    return wrapper


# Set active folder to writable one if it is not
def writer(func):
    @wraps(func)
    @restore_session_values
    def wrapper(self, *args, **kwargs):
        if self._active_folder == '/':
            self.active_folder = '/Common'

            return func(self, *args, **kwargs)

    return wrapper


# Wrap a method inside a transaction
def transaction(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        our_transaction = not self.transaction

        if our_transaction:
            # Start a transaction
            self.transaction = True

        try:
            func_ret = func(self, *args, **kwargs)
        except:
            # try to roll back
            try:
                if our_transaction:
                    self.transaction = False
            except:
                pass

        if our_transaction:
            self._submit_transaction()


###########################################################################
# Loadbalancer
###########################################################################
class Lb(object):
    _version = 11

    def __init__(self, host, username, password, versioncheck=True):

        self._host         = host
        self._username     = username
        self._versioncheck = versioncheck

        self._transport = bigsuds.BIGIP(host, username, password)
        version = self._transport.System.SystemInfo.get_version()
        if versioncheck and not 'BIG-IP_v11' in version:
            raise UnsupportedF5Version('This class only supports BIG-IP v11', version)

        self._active_folder       = self.active_folder
        self._recursive_query     = self.recursive_query
        self._transaction         = self.transaction
        self._transaction_timeout = self.transaction_timeout

    def __repr__(self):
        return "f5.Lb('%s')" % (self._host)

    ###########################################################################
    # Properties
    ###########################################################################
    @property
    def host(self):
        return self._host

    @property
    def username(self):
        return self._username

    @property
    def versioncheck(self):
        return self._versioncheck

    #### active_folder ####
    @property
    def active_folder(self):
        self._active_folder = self._get_active_folder()
        return self._active_folder

    @active_folder.setter
    def active_folder(self, value):
        self._set_active_folder(value)
        self._active_folder =  value

    #### recursive_query ####
    @property
    def recursive_query(self):
        recursive_query_state = self._get_recursive_query_state()
        if recursive_query_state == 'STATE_ENABLED':
            self._recursive_query =  True
        elif recursive_query_state == 'STATE_DISABLED':
            self._recursive_query =  False
        else:
            raise RuntimeError('Unknown status %s received for recursive_query_state') % (recursive_query_state)

        return self._recursive_query

    @recursive_query.setter
    def recursive_query(self, value):
        if value == True:
            recursive_query_state = 'STATE_ENABLED'
        elif value == False:
            recursive_query_state = 'STATE_DISABLED'
        else:
            raise ValueError('recursive_query must be one of True/False, not %s' % (value))

        self._set_recursive_query_state(recursive_query_state)
        self._recursive_query = value

    #### transaction ####
    @property
    def transaction(self):
        self._transaction = self._active_transaction()
        return self._transaction

    @transaction.setter
    def transaction(self,value):
        if value == True:
            self._ensure_transaction()
            self._transaction = True
        elif value == False:
            self._ensure_no_transaction()
            self._transaction = False

    #### transaction_timeout ####
    @property
    def transaction_timeout(self):
        self._transaction_timeout = self._get_transaction_timeout()
        return self._transaction_timeout

    @transaction_timeout.setter
    def transaction_timeout(self, value):
        self._set_transaction_timeout(value)
        self._transaction_timeout = value

    ###########################################################################
    # INTERNAL API
    ###########################################################################

    #### Session methods ####
    def _ensure_transaction(self):
        wsdl = self._transport.System.Session
        try:
            wsdl.start_transaction()
        except ServerError as e:
            if 'Only one transaction can be open at any time' in e.message:
                pass
            else:
                raise

    def _ensure_no_transaction(self):
        wsdl = self._transport.System.Session
        try:
            wsdl.rollback_transaction()
        except ServerError as e:
            if 'No transaction is open to roll back.' in e.message:
                pass
            else:
                raise
        except:
            raise

    def _submit_transaction(self):
        wsdl = self._transport.System.Session
        wsdl.submit_transaction()

    def _rollback_transaction(self):
        wsdl = self._transport.System.Session
        wsdl.rollback_transaction()

    def _get_transaction_timeout(self):
        wsdl = self._transport.System.Session
        return wsdl.get_transaction_timeout()

    def _set_transaction_timeout(self, value):
        wsdl = self._transport.System.Session
        wsdl.set_transaction_timeout(value)

    # Currently the only way of finding out if there's an active transaction
    # is to actually try starting another one :/
    def _active_transaction(self):
        wsdl = self._transport.System.Session
        try:
            wsdl.start_transaction()
        except ServerError as e:
            if 'Only one transaction can be open at any time' in e.message:
                return True
            else:
                raise

        wsdl.rollback_transaction()
        return False

    def _get_active_folder(self):
        wsdl = self._transport.System.Session
        return wsdl.get_active_folder()

    def _set_active_folder(self, folder):
        wsdl = self._transport.System.Session
        return wsdl.set_active_folder(folder)

    def _get_recursive_query_state(self):
        wsdl = self._transport.System.Session
        return wsdl.get_recursive_query_state()

    def _set_recursive_query_state(self, state):
        wsdl = self._transport.System.Session
        wsdl.set_recursive_query_state(state)

    #### Node methods ####
    def _node_cache_put(self, node):
        self._node_cache[node.name] = node

    def _node_cache_get(self, name):
        if name in self._node_cache:
            return self._node_cache[name]

    #### Pool methods ####
    def _pool_cache_put(self, pool):
        self._pool_cache[pool.name] = pool

    def _pool_cache_get(self, name):
        if name in self._pool_cache:
            return self._pool_cache[name]

    def _pm_cache_get(self, node_name, port, pool_name):
        key = '%s%s%s' % (node_name, port, pool_name)
        if key in self._pm_cache:
            return self._pm_cache[key]

    def _pm_cache_put(self, pm):
        key = '%s%s%s' % (pm.node.name, pm.port, pm.pool.name)
        self._pm_cache[key] = pm

    #### VirtualServer methods ####
    def _vs_cache_put(self, vs):
        self._vs_cache[vs.name] = vs

    def _vs_cache_get(self, name):
        if name in self._vs_cache:
            return self._vs_cache[name]

    #### Rule methods
    def _rule_cache_put(self, rule):
        self._rule_cache[rule.name] = rule

    def _rule_cache_get(self, name):
        if name in self._rule_cache:
            return self._rule_cache[name]

    ###########################################################################
    # PUBLIC API
    ###########################################################################
    def submit_transaction(self):
        self._submit_transaction()
    
    def pool_get(self, name):
        """Returns a single F5 pool"""
        pool = f5.Pool.factory.get(name, self)
        pool.refresh()

        return pool

    @recursivereader
    def pools_get(self, pattern=None, minimal=False):
        """Returns a list of F5 Pools, takes optional pattern"""
        return f5.Pool._get(self, pattern, minimal)

    def pm_get(self, node, port, pool):
        """Returns a single F5 PoolMember"""
        pm = f5.PoolMember.factory.get(node, port, pool, self)
        pm.refresh()

        return pm

    @recursivereader
    def pms_get(self, pools=None, pattern=None, minimal=False):
        """Returns a list of F5 PoolMembers, takes optional list of pools and pattern"""
        return f5.PoolMember._get(self, pools, pattern, minimal)

    def node_get(self, name):
        """Returns a single F5 Node"""
        node = f5.Node.factory.get(name, self)
        node.refresh()

        return node

    @recursivereader
    def nodes_get(self, pattern=None, minimal=False):
        """Returns a list of F5 Nodes, takes optional list of pools and pattern"""
        return f5.Node._get(self, pattern, minimal)

    def rule_get(self, name):
        """Returns a single F5 Rule"""
        rule = f5.Rule.factory.get(name, self)
        rule.refresh()

        return rule

    @recursivereader
    def rules_get(self, pattern=None, minimal=False):
        """Returns a list of F5 Rules, takes optional pattern"""
        return f5.Rule._get(self, pattern, minimal)

    def vs_get(self, name):
        """Returns a single F5 VirtualServer"""
        vs = f5.VirtualServer.factory.get(name, self)
        vs.refresh()

        return vs

    @recursivereader
    def vss_get(self, pattern=None, minimal=False):
        """Returns a list of F5 VirtualServers, takes optional pattern"""
        return f5.VirtualServer._get(self, pattern, minimal)

    @recursivereader
    def pools_get_vs(self, pools=None, minimal=False):
        """Returns VirtualServers associated with a list of Pools"""
        if pools is None:
            pools = f5.Pool._get_list(self)
        else:
            if isinstance(pools[0], f5.Pool):
                pools = [pool.name for pool in pools]

        result = {pool: [] for pool in pools}

        vss = f5.VirtualServer._get(self, minimal=minimal)
        if minimal is True:
            vss = f5.VirtualServer._refresh_default_pool(self, vss)

        for pool in pools:
            for vs in vss:
                if pool == vs._default_pool.name:
                    result[pool].append(vs)

        return result

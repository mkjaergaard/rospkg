# Software License Agreement (BSD License)
#
# Copyright (c) 2011, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Willow Garage, Inc. nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import os
import sys

from .common import MANIFEST_FILE, STACK_FILE, ROS_STACK, ResourceNotFound
from .environment import get_ros_paths, get_ros_home
from .manifest import parse_manifest_file, InvalidManifest

def _read_rospack_cache(cache_path, cache, ros_paths):
    """
    Read in rospack/rosstack cache data into cache. On-disk cache
    specifies a ROS_ROOT and ROS_PACKAGE_PATH, which must match the
    requested environment.
    
    :param cache_path: path to cache file, e.g. ~/.ros/rospack_cache
    :param cache: empty dictionary to store package list in. 
        The format of the cache is {package_name: file_path}. ``{str: str}``
    :param ros_paths: Ordered list of paths to validate cache with, ``[str]``
    :returns: ``True`` if on-disk cache matches and was loaded,
      ``False`` otherwise.  If ``False``, *cache* will be emptied,
      ``bool``
    """
    if not os.path.exists(cache_path):
        return False

    cached_ros_paths = []
    with open(cache_path) as f:
        for l in f.readlines():
            l = l[:-1]
            if not len(l):
                continue
            if l[0] == '#':
                # check that the cache matches our env
                if l.startswith('#ROS_ROOT='):
                    cached_ros_paths.insert(0, l[len('#ROS_ROOT='):])
                elif l.startswith('#ROS_PACKAGE_PATH='):
                    paths = l[len('#ROS_PACKAGE_PATH='):].split(os.pathsep)
                    paths = [x for x in paths if x]
                    cached_ros_paths.extend(paths)
            else:
                cache[os.path.basename(l)] = l

    if not ros_paths == cached_ros_paths:
        cache.clear()
        return False
    return True
    
def list_by_path(manifest_name, path, cache):
    """
    List ROS stacks or packages within the specified path.

    The cache will be updated with the resource->path
    mappings. list_by_path() does NOT returned cached results
    -- it only updates the cache.
    
    :param manifest_name: MANIFEST_FILE or STACK_FILE, ``str``
    :param path: path to list resources in, ``str``
    :param cache: path cache to update. Maps resource name to directory path, ``{str: str}``
    :returns: complete list of resources in ROS environment, ``[str]``
    """
    resources = []
    path = os.path.abspath(path)
    basename = os.path.basename
    for d, dirs, files in os.walk(path, topdown=True, followlinks=True):
        if manifest_name in files:
            resource_name = basename(d)
            if resource_name not in resources:
                resources.append(resource_name)
                if cache is not None:
                    cache[resource_name] = d
            del dirs[:]
            continue #leaf
        elif MANIFEST_FILE in files:
            # noop if manifest_name==MANIFEST_FILE, but a good
            # optimization for stacks.
            del dirs[:]
            continue #leaf     
        elif 'rospack_nosubdirs' in files:
            del dirs[:]
            continue  #leaf
        # remove hidden dirs (esp. .svn/.git)
        [dirs.remove(di) for di in dirs if di[0] == '.']
    return resources

class ManifestManager(object):
    """
    Base class implementation for :class:`RosPack` and
    :class:`RosStack`.  This class indexes resources on paths with
    where manifests denote the precense of the resource.  NOTE: for
    performance reasons, instances cache information and will not
    reflect changes made on disk or to environment configuration.
    """
    
    def __init__(self, manifest_name, cache_name,
                 ros_paths=None):
        """
        ctor. subclasses are expected to use *manifest_name* and
        *cache_name* to customize behavior of ManifestManager.
        
        :param manifest_name: MANIFEST_FILE or STACK_FILE
        :param cache_name: rospack_cache or rosstack_cache
        :param ros_paths: Ordered list of paths to search for
          resources. If `None` (default), use environment ROS path.
        """
        self._manifest_name = manifest_name
        self._cache_name = cache_name

        if ros_paths is None:
            self._ros_paths = get_ros_paths()
        else:
            self._ros_paths = ros_paths
        
        self._manifests = {}
        self._depends_cache = {}
        self._rosdeps_cache = {}
        self._location_cache = None

    def get_ros_paths(self):
        return self._ros_paths[:]
    ros_paths = property(get_ros_paths, doc="Get ROS paths of this instance")

    def get_manifest(self, name):
        """
        :raises: :exc:`InvalidManifest`
        """
        if name in self._manifests:
            return self._manifests[name]
        else:
            return self._load_manifest(name)
            
    def _update_location_cache(self):
        if self._location_cache is not None:
            return
        # initialize cache
        cache = self._location_cache = {}
        # nothing to search, #3680
        if not self._ros_paths:
            return
        # - first attempt to read .rospack_cache
        cache_path = os.path.join(get_ros_home(), self._cache_name)
        if _read_rospack_cache(cache_path, cache, self._ros_paths):
            return list(cache.keys()) #py3k
        # - else, crawl paths using our own logic, in reverse order to get correct precedence
        for path in reversed(self._ros_paths):
            list_by_path(self._manifest_name, path, cache)
    
    def list(self):
        """
        List resources.

        :returns: complete list of package names in ROS environment, ``[str]``
        """
        self._update_location_cache()
        return self._location_cache.keys()

    def get_path(self, name):
        """
        :param name: package name, ``str``
        :returns: filesystem path of package
        :raises: :exc:`ResourceNotFound`
        """
        self._update_location_cache()
        if not name in self._location_cache:
            raise ResourceNotFound(name, ros_paths=self._ros_paths)
        else:
            return self._location_cache[name]
        
    def _load_manifest(self, name):
        """
        :raises: :exc:`ResourceNotFound`
        """
        retval = self._manifests[name] = parse_manifest_file(self.get_path(name), self._manifest_name)
        return retval
        
    def get_depends(self, name, implicit=True):
        """
        Get dependencies of a resource.  If implicit is ``True``, this
        includes implicit (recursive) dependencies.

        :param name: resource name, ``str``
        :param implicit: include implicit (recursive) dependencies, ``bool``

        :returns: list of names of dependencies, ``[str]``
        :raises: :exc:`InvalidManifest` If resource or any of its
          dependencies have an invalid manifest.
        """
        if not implicit:
            m = self.get_manifest(name)
            return [d.name for d in m.depends]
        else:
            if name in self._depends_cache:
                return self._depends_cache[name]

            # take the union of all dependencies
            names = [p.name for p in self.get_manifest(name).depends]

            # assign key before recursive call to prevent infinite case
            self._depends_cache[name] = s = set()

            for p in names:
                s.update(self.get_depends(p, implicit))
            # add in our own deps
            s.update(names)
            # cache the return value as a list
            s = list(s)
            self._depends_cache[name] = s
            return s
    
    def get_depends_on(self, name, implicit=True):
        """
        Get resources that depend on a resource.  If implicit is ``True``, this
        includes implicit (recursive) dependency relationships.

        NOTE: this does *not* raise :exc:`rospkg.InvalidManifest` if
        there are invalid manifests found.

        :param name: resource name, ``str``
        :param implicit: include implicit (recursive) dependencies, ``bool``

        :returns: list of names of dependencies, ``[str]``
        """
        depends_on = []
        if not implicit:
            # have to examine all dependencies
            for r in self.list():
                if r == name:
                    continue
                try:
                    m = self.get_manifest(r)
                    if any(d for d in m.depends if d.name == name):
                        depends_on.append(r)
                except InvalidManifest:
                    # robust to bad packages
                    pass
        else:
            # Computing implicit dependencies requires examining the
            # dependencies of all packages.  As we already implement
            # this logic in get_depends(), we simply reuse it here for
            # the reverse calculation.  This enables us to use the
            # same dependency cache that get_depends() uses.  The
            # efficiency is roughly the same due to the caching.
            for r in self.list():
                if r == name:
                    continue
                try:
                    depends = self.get_depends(r, implicit=True)
                    if name in depends:
                        depends_on.append(r)
                except InvalidManifest:
                    # robust to bad packages
                    pass
        return depends_on

class RosPack(ManifestManager):
    """
    Utility class for querying properties about ROS packages. This
    should be used when querying properties about multiple
    packages.

    NOTE 1: for performance reasons, RosPack caches information about
    packages.

    NOTE 2: RosPack is not thread-safe. 

    Example::
      rp = RosPack()
      packages = rp.list_packages()
      path = rp.get_path('rospy')
      depends = rp.get_depends('roscpp')
      direct_depends = rp.get_depends('roscpp', implicit=False)
    """
    
    def __init__(self, ros_paths=None):
        """
        :param ros_paths: Ordered list of paths to search for
          resources. If `None` (default), use environment ROS path.
        """
        super(RosPack, self).__init__(MANIFEST_FILE,
                                      'rospack_cache',
                                      ros_paths)
        self._rosdeps_cache = {}

    def get_rosdeps(self, package, implicit=True):
        """
        Collect rosdeps of specified package into a dictionary.
        
        :param package: package name, ``str``
        :param implicit: include implicit (recursive) rosdeps, ``bool``
        
        :returns: list of rosdep names, ``[str]``
        """
        if implicit:
            return self._implicit_rosdeps(package)
        else:
            m = self.get_manifest(package)
            return [d.name for d in m.rosdeps]
        
    def _implicit_rosdeps(self, package):
        """
        Compute recursive rosdeps of a single package and cache the
        result in self._rosdeps_cache.

        :param package: package name, ``str``
        :returns: list of rosdeps, ``[str]``
        """
        if package in self._rosdeps_cache:
            return self._rosdeps_cache[package]

        # set the key before recursive call to prevent infinite case
        self._rosdeps_cache[package] = s = set()

        # take the union of all dependencies
        packages = self.get_depends(package, implicit=True)
        for p in packages:
            s.update(self.get_rosdeps(p, implicit=False))
        # add in our own deps
        m = self.get_manifest(package)
        s.update([d.name for d in m.rosdeps])
        # cache the return value as a list
        s = list(s)
        self._rosdeps_cache[package] = s
        return s
        
    def stack_of(self, package):
        """
        :param package: package name, ``str``
        :returns: name of stack that package is in, or None if package is not part of a stack, ``str``
        :raises: :exc:`ResourceNotFound` If package cannot be located
        """
        d = self.get_path(package)
        while d and os.path.dirname(d) != d:
            stack_file = os.path.join(d, STACK_FILE)
            if os.path.exists(stack_file):
                return os.path.basename(d)
            else:
                d = os.path.dirname(d)

class RosStack(ManifestManager):
    """
    Utility class for querying properties about ROS stacks. This
    should be used when querying properties about multiple
    stacks.

    NOTE 1: for performance reasons, RosStack caches information about
    stacks.

    NOTE 2: RosStack is not thread-safe. 
    """
    
    def __init__(self, ros_paths=None):
        """
        :param ros_paths: Ordered list of paths to search for
          resources. If `None` (default), use environment ROS path.
        """
        super(RosStack, self).__init__(STACK_FILE, 'rosstack_cache',
                                       ros_paths)
            
    def packages_of(self, stack):
        """
        :returns: name of packages that are part of stack, ``[str]``
        :raises: :exc:`ResourceNotFound` If stack cannot be located
        """
        return list_by_path(MANIFEST_FILE, self.get_path(stack), {})

    def get_stack_version(self, stack):
        """
        :param env: override environment variables, ``{str: str}``
        :returns: version number of stack, or None if stack is unversioned, ``str``
        """
        return get_stack_version_by_dir(self.get_path(stack))

# #2022
def expand_to_packages(names, rospack, rosstack):
    """
    Expand names into a list of packages. Names can either be of packages or stacks.

    :param names: names of stacks or packages, ``[str]``
    :returns: ([packages], [not_found]). Returns two lists. The first
      is of packages names. The second is a list of names for which no
      matching stack or package was found. Lists may have
      duplicates. ``([str], [str])``
    """
    if type(names) not in (tuple, list):
        raise ValueError("names must be a list of strings")

    # do full package list first. This forces an entire tree
    # crawl. This is less efficient for a small list of names, but
    # much more efficient for many names.
    package_list = rospack.list()
    valid = []
    invalid = []
    for n in names:
        if not n in package_list:
            try:
                valid.extend(rosstack.packages_of(n))
            except ResourceNotFound as e:
                invalid.append(n)
        else:
            valid.append(n)
    return valid, invalid

def get_stack_version_by_dir(stack_dir):
    """
    Get stack version where stack_dir points to root directory of stack.
    
    :param env: override environment variables, ``{str: str}``

    :returns: version number of stack, or None if stack is unversioned, ``str``
    """
    # REP 109: check for <version> tag first, then CMakeLists.txt
    manifest_filename = os.path.join(stack_dir, STACK_FILE)
    if os.path.isfile(manifest_filename):
        m = parse_manifest_file(stack_dir, STACK_FILE)
        if m.version:
            return m.version
    
    cmake_filename = os.path.join(stack_dir, 'CMakeLists.txt')
    if os.path.isfile(cmake_filename):
        with open(cmake_filename) as f:
            try:
                return _get_cmake_version(f.read())
            except ValueError:
                return None
    else:
        return None

def _get_cmake_version(text):
    """
    :raises :exc:`ValueError` If version number in CMakeLists.txt cannot be parsed correctly
    """
    import re
    for l in text.split('\n'):
        if l.strip().startswith('rosbuild_make_distribution'):
            x_re = re.compile(r'[()]')
            lsplit = x_re.split(l.strip())
            if len(lsplit) < 2:
                raise ValueError("couldn't find version number in CMakeLists.txt:\n\n%s"%l)
            version = lsplit[1]
            if version:
                return version
            else:
                raise ValueError("cannot parse version number in CMakeLists.txt:\n\n%s"%l)

def get_package_name(path):
    """
    Get the name of the ROS package that contains *path*. This is
    determined by finding the nearest parent ``manifest.xml`` file.
    This routine may not traverse package setups that rely on internal
    symlinks within the package itself.
    
    :param path: filesystem path
    :return: Package name or ``None`` if package cannot be found, ``str``
    """
    #NOTE: the realpath is going to create issues with symlinks, most
    #likely.
    parent = os.path.dirname(os.path.realpath(path))
    #walk up until we hit ros root or ros/pkg
    while not os.path.exists(os.path.join(path, MANIFEST_FILE)) and parent != path:
        path = parent
        parent = os.path.dirname(path)
    # check termination condition
    if os.path.exists(os.path.join(path, MANIFEST_FILE)):
        return os.path.basename(os.path.abspath(path))
    else:
        return None
    

import math
import os
import shutil
import sys
import warnings
import sklearn.preprocessing
import numpy as np
from joblib import load, dump
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from scipy.optimize import linear_sum_assignment
#matplotlib.use('Agg')

FILENAME = "staNMFDicts_"


def load_example(reweight=False):
    '''
    Loads full data matrix from WuExampleExpression.csv file into numpy array;
    weights column names by their number of replicate occurances

    Parameters
    ----------
    reweight : bool, optional with default False
        whether to reweight the data matrix by 1 / occurences

    Returns
    -------
    X : array, shape (n_samples, n_features)
        data matrix extracted from WuExampleExpression.csv

    Examples
    --------
    >>> X = load_example()

    '''

    workingmatrix = pd.read_csv('../Demo/WuExampleExpression.csv', index_col=0)

    if reweight:
        # weight each column (gene) by 1 / its occurences in replicates
        colnames = workingmatrix.columns.values
        colnames = [(str(x).split('.'))[0] for x in colnames]
        weight = np.zeros(len(colnames))

        for i in range(len(colnames)):
            weight[i] = 1/colnames.count(colnames[i]) ** .5

        workingmatrix = workingmatrix.apply(
            lambda x: weight * x,
            axis=1,
        )

    X = (np.array(workingmatrix).astype(float)).T
    return X


def findcorrelation(A, B):
    '''
    Construct k by k matrix of Pearson product-moment correlation
    coefficients for every combination of two columns in A and B

    Parameters
    ----------
    A : array, shape (n_features, n_components)
        first NMF solution matrix

    B : array, shape (n_features, n_components)
        second NMF solution matrix, of same dimensions as A

    Returns
    -------
    X : array shape (n_components, n_components)
        array[a][b] is the correlation between column 'a' of X
        and column 'b'

    '''
    A_std = sklearn.preprocessing.normalize(A, axis=0)
    B_std = sklearn.preprocessing.normalize(B, axis=0)
    return A_std.T @ B_std


def amariMaxError(correlation):
    '''
    Compute what Wu et al. (2016) described as a 'amari-type error'
    based on average distance between factorization solutions

    Parameters
    ----------
    correlation: array, shape (n_components, n_components)
        cross correlation matrix

    Returns
    -------
    distM : double/float
        Amari distance

    '''

    n, m = correlation.shape
    maxCol = np.absolute(correlation).max(0)
    colTemp = np.mean((1-maxCol))
    maxRow = np.absolute(correlation).max(1)
    rowTemp = np.mean((1-maxRow))
    distM = (rowTemp + colTemp)/(2)

    return distM


def HungrianError(correlation):
    '''
    Compute error via Hungrian error
    based on average distance between factorization solutions

    Parameters
    ----------
    correlation: array, shape (n_components, n_components)
        cross correlation matrix

    Returns
    -------
    distM : double/float
        Hugrian distance

    '''

    n, m = correlation.shape
    correlation = np.absolute(correlation)  # ignore the sign of corr
    x, y = linear_sum_assignment(-correlation)
    distM = np.mean([1 - correlation[xx, yy] for xx, yy in zip(x, y)])
    return distM


# Define a worker function useful for parallelism
def f(model, X, K, seed, l, path):
    # set the number of components
    model.set_n_components(K)
    # set the random state
    if seed is not None:
        model.random_state = seed + 100 * l
    model.fit(X)  # fit nmf model
    # write model to a joblib file in the path folder
    outputfilename = (
        "nmf_model_" + model.__class__.__name__
        + '_' + str(l) + ".joblib"
    )
    outputfilepath = os.path.join(path, outputfilename)
    dump(model, outputfilepath)


class staNMF:
    '''Python 3 implementation of Siqi Wu's 03/2016 Stability NMF (staNMF)

    Solves non-negative matrix factorization for a range of principal patterns
    (PPs) with either different initializations or bootstrapped samples.

    Parameters
    ----------
    X : numpy array, shape (n_samples, n_features)
        2d numpy array containing the data.

    folderID : str, optional with default ""
        allows user to specify a unique (to the user's working directory)
        identifier for the FILENAME folder that the runNMF method creates.

    processes : int, optional with default 3
        the number of processes to use when parallel is True

    K1 : int, optional with default 15
        lowest number of PP's (K) tested

    K2 : int, optional with default 30
        highest number of PP's (K) tested

    seed : int, optional with default 123
        set numpy random seed

    replicates : int or tuple of ints of length 2, optional with default
    int 100
        specify the bootstrapped repetitions to be performed on each value of K
        for use in stability analysis; if a list of length 2: self.replicates
            is set to a list of ints between the first and second elements of
            this tuple. If int: self.replicates is set to range(integer).

    NMF_finished : bool, optional with default False
        True if runNMF has been completed for the dataset. To surpass NMF step
        if fileID file already contains factorization solutions for X in your
        range [K1, K2], set to True.

    parallel_mode : Str, optional with default "sequential"
        "sequential": Each task runs sequentially.
        "multiprocessing": Use multiprocessing for parallel computation.
        "pyspark": Use pyspark to do parallel computation.

    chunksize : int, optional with default 10
        the smallest number of tasks to assign to each worker

    '''

    def __init__(self, X, folderID="", K1=15, K2=30,
                 seed=123, replicates=100, processes=3,
                 NMF_finished=False, parallel_mode="sequential",
                 chunksize=10):
        warnings.filterwarnings("ignore")
        self.K1 = K1
        self.K2 = K2
        self.seed = seed
        self.guess = np.array([])
        self.guessdict = {}
        self.parallel_mode = parallel_mode
        self.processes = processes
        if isinstance(replicates, int):
            self.replicates = range(replicates)
        elif isinstance(replicates, tuple):
            start, stop = replicates
            self.replicates = range(replicates[0], replicates[1])
        self.X = X
        self.folderID = folderID
        self.NMF_finished = NMF_finished
        self.instabilitydict = {}
        self.instability_std = {}
        self.instabilityarray = []
        self.instabilityarray_std = []
        self.stability_finished = False
        self.chunksize = chunksize

    def runNMF(self, nmf_model):
        '''
        Iterate through range of integers between the K1 and K2 provided (By
        default, K1=15 and K2=30), run NMF using the model; output NMF matrix
        files (.csv form).

        Parameters
        ----------
        nmf_model : a model that can be used to fit NMF models

        Returns
        -------
        None

        Side effects
        ------------
        (k2-k1) folders, each containing files for every replicate
        (labeled nmf_model_<nmf_class>_<replicate>.joblib).

        Raises
        ------
        OSError
            the path cannot be created.

        '''

        self.NMF_finished = False
        numPatterns = np.arange(self.K1, self.K2+1)
        if self.parallel_mode == "multiprocessing":
            from multiprocessing import Pool
            pool = Pool(self.processes)
        elif self.parallel_mode == 'pyspark':
            import pyspark
            from pyspark.sql.functions import (
                pandas_udf,
                PandasUDFType,
                explode,
                lit,
                array,
            )
            from pyspark.sql.types import (
                IntegerType,
                FloatType,
                ArrayType,
                StructField,
                StructType,
            )
            spark = pyspark.sql.SparkSession.builder \
                .master("local") \
                .appName("Demo") \
                .getOrCreate()
            sc = spark.sparkContext
            sc.addPyFile("../staNMF/nmf_models/sklearn_nmf.py")

        for k in range(len(numPatterns)):
            K = numPatterns[k]
            path = (
                "./" + FILENAME + self.folderID + "/K=" + str(K) + "/"
            )
            try:
                os.makedirs(path)
            except OSError:
                if not (os.path.isdir(path)):
                    raise
            m, n = np.shape(self.X)

            print("Working on K = {}...".format(K))

            if self.parallel_mode == 'sequential':
                # fit nmf_models
                for l in self.replicates:
                    # set the number of components
                    nmf_model.set_n_components(K)
                    # set the random state
                    if self.seed is not None:
                        nmf_model.random_state = self.seed + 100 * l
                    nmf_model.fit(self.X)  # fit nmf model
                    # write model to a joblib file in the path folder
                    outputfilename = (
                        "nmf_model_" + nmf_model.__class__.__name__
                        + '_' + str(l) + ".joblib"
                    )
                    outputfilepath = os.path.join(path, outputfilename)
                    dump(nmf_model, outputfilepath)
            elif self.parallel_mode == 'multiprocessing':
                parameters = [
                    (nmf_model, self.X, K, self.seed, l, path)
                    for l in self.replicates
                ]
                pool.starmap(f, parameters, chunksize=self.chunksize)
            elif self.parallel_mode == 'pyspark':
                sqlCtx = pyspark.sql.SQLContext(spark)
                spark_df = sqlCtx.createDataFrame(
                    pd.DataFrame(
                        self.X,
                        columns=['F{}'.format(i) for i in range(n)],
                        dtype=float,
                    )
                )
                seed = array([
                    lit(i) for i in self.replicates if i % self.chunksize == 0
                ])
                spark_df = spark_df.withColumn("seed", seed)
                spark_df = spark_df.withColumn("seed", explode(spark_df.seed))
                spark_df = spark_df.repartitionByRange(self.processes,"seed")
                K_broadcast = sc.broadcast(K)
                params_broadcast = sc.broadcast(nmf_model.get_params())
                chunksize_broadcast = sc.broadcast(self.chunksize)
                output_schema = StructType([
                    StructField('seed', IntegerType(), True),
                    StructField('l2_error', FloatType(), True),
                    StructField('components', ArrayType(FloatType()), True)
                ])
                @pandas_udf(output_schema, PandasUDFType.GROUPED_MAP)
                def fit_sklearn_nmf(df):
                    from sklearn_nmf import sklearn_nmf
                    import numpy as np
                    out = []
                    for i in range(df.seed[0], df.seed[0] + chunksize_broadcast.value):
                        ml = sklearn_nmf()
                        ml.set_params(**params_broadcast.value)
                        ml.n_components=K_broadcast.value
                        ml.random_state=i
                        X = np.array(df.drop("seed", axis=1))
                        ml.fit(X)
                        coefs = ml.transform(X)
                        l2_error = np.sum((coefs @ ml.components_ - X) ** 2) / np.sum(X**2)
                        out.append([ml.random_state, l2_error, ml.components_.flatten()])
                    return pd.DataFrame(out, columns=['seed', 'l2_error', 'components'])
                result = spark_df.groupBy("seed").apply(fit_sklearn_nmf).toPandas()
                # Record all the results
                # set the number of components
                nmf_model.set_n_components(K)
                for l in result.index:
                    # set the random state
                    nmf_model.random_state = result.loc[l, 'seed']
                    nmf_model.l2_error = result.loc[l, 'l2_error']
                    nmf_model.components_ = np.array(result.loc[l, 'components']).reshape((K, n))
                    # write model to a joblib file in the path folder
                    outputfilename = (
                            "nmf_model_" + nmf_model.__class__.__name__
                            + '_' + str(l) + ".joblib"
                    )
                    outputfilepath = os.path.join(path, outputfilename)
                    dump(nmf_model, outputfilepath)

            else:
                raise ValueError(
                    "self.parallel_mode({}) is not acceptable.".format(
                        self.parallel_mode
                    )
                )

        self.NMF_finished = True
        if self.parallel_mode == 'multiprocessing':
            pool.close()
        elif self.parallel_mode == 'pyspark':
            spark.stop()

    def instability(self, tag, k1=0, k2=0):
        '''
        Performs instability calculation for NMF models for each K
        within the range entered

        Parameters
        ----------
        tag : str
            the name of the nmf model to compute the stability

        k1 : int, optional, default self.K1
            lower bound of K to compute stability

        k2 : int, optional, default self.K2
            upper bound of K to compute instability

        Returns
        -------
        None

        Side effects
        ------------
        "instability.csv" containing instability index
        for each K between and including k1 and k2; updates
        self.instabilitydict (required for makeplot())
        '''
        if k1 == 0:
            k1 = self.K1
        if k2 == 0:
            k2 = self.K2

        numReplicates = len(self.replicates)

        if self.NMF_finished is False:
            print("staNMF Error: runNMF is not complete\n")
        else:
            numPatterns = np.arange(k1, k2+1)
            n_features = self.X.shape[1]
            # loop through each number of PPs
            for k in numPatterns:
                print("Calculating instability for " + str(k))
                # load the dictionaries
                path = (
                    "./" + FILENAME + self.folderID + "/K=" + str(k)+"/"
                )
                Dhat = np.zeros((numReplicates, n_features, k))

                for replicate in range(numReplicates):
                    inputfilename = (
                        "nmf_model_" + tag
                        + "_" + str(replicate) + ".joblib"
                    )
                    inputfilepath = os.path.join(path, inputfilename)
                    model = load(inputfilepath)
                    Dhat[replicate] = model.components_.T

                # compute the distance matrix between each pair of dicts
                distMat = np.zeros(shape=(numReplicates, numReplicates))

                for i in range(numReplicates):
                    for j in range(i, numReplicates):
                        x = Dhat[i]
                        y = Dhat[j]

                        CORR = findcorrelation(x, y)
                        distMat[i][j] = HungrianError(CORR)
                        distMat[j][i] = distMat[i][j]

                # compute the instability and the standard deviation
                self.instabilitydict[k] = (
                    np.sum(distMat) / (numReplicates * (numReplicates-1))
                )
                # The standard deviation of the instability is tricky:
                # It is a U-statistic and in general hard to compute std.
                # Fortunately, there is a easy-to-understand upper bound.
                self.instability_std[k] = (
                    np.sum(distMat ** 2)
                    / (numReplicates * (numReplicates - 1))
                    - self.instabilitydict[k] ** 2
                ) ** .5 * (2 / distMat.shape[0]) ** .5
                # write the result into csv file
                outputfile = path + "instability.csv"
                pd.DataFrame({
                    'K': [k],
                    'instability': [self.instabilitydict[k]],
                    'instability_std': [self.instability_std[k]],
                }).to_csv(outputfile, mode='a', header=False, index=False)
        # set the stability_finished to be True
        self.stability_finished = True

    def get_instability(self):
        '''
        Retrieves instability values calculated in this instance of staNMF

        Returns
        -------
        self.instabilitydict : dict
            dictionary with keys K, values instability index

        '''

        if self.stability_finished:
            return self.instabilitydict
        else:
            print("Instability has not yet been calculated for your NMF"
                  "results. Use staNMF.instability() to continue.")

    def plot(self, dataset_title="Drosophila Spatial Expression Data", xmax=0,
             xmin=-1, ymin=0, ymax=0, xlab="K", ylab="Instability Index"):
        '''
        Plots instability results for all K's between and including K1 and K2
        with K on the X axis and instability on the Y axis

        Parameters
        ----------

        dataset_title : str, optional, default "Drosophila
            Expression Data"
            The title used in the plot

        ymax : float, optional,  default
            largest Y + largest std(Y) * 2  + (largest Y/ # of points)
            the maximum y axis limit

        xmax : float, optional, default K2+1

        xlab : string, default "K"
            x-axis label

        ylab : string, default "Instability Index"
            y-axis label

        Returns
        -------
        None

        Side effects
        ------------
        A png file named <dataset_title>.png is saved.

        '''
        kArray = []
        self.instabilityarray = []
        self.instabilityarray_std = []
        for K in range(self.K1, self.K2+1):
            kpath = (
                "./" + FILENAME + "{}/K={}/instability.csv"
            ).format(self.folderID, K)
            df = pd.read_csv(kpath, header=None, index_col=False)
            kArray.append(int(df.iloc[-1, 0]))
            self.instabilityarray.append(float(df.iloc[-1, 1]))
            self.instabilityarray_std.append(float(df.iloc[-1, 2]))
        if xmax == 0:
            xmax = self.K2 + 1
        if xmin == -1:
            xmin = self.K1 - .1
        if ymax == 0:
            ymax = max(self.instabilityarray) \
                   + max(self.instabilityarray_std) * 2 \
                   + (max(self.instabilityarray) / len(self.instabilityarray))
        plt.errorbar(x=kArray,
                     y=self.instabilityarray,
                     yerr=np.array(self.instabilityarray_std)*2)
        plt.axis([xmin, xmax, ymin, ymax])
        plt.xlabel(xlab)
        plt.ylabel(ylab)
        plt.axes.titlesize = 'smaller'
        plt.title(str(dataset_title))
        plotname = str(dataset_title + ".png")
        plt.savefig(plotname)

    def ClearDirectory(self, k_list):
        '''
        A storage-saving option that clears the entire directory of each K
        requested, including the instability.csv file in each folder

        Parameters
        ----------
        k_list : list
            list of K's to delete corresponding directories of

        Notes
        -----
        This should only be used after stability has been calculated for
        each K you wish to delete.
        '''

        for K in k_list:
            path = ("./" + FILENAME + "{}/K={}/").format(self.folderID, K)
            shutil.rmtree(path)

import cv2
import numpy as np
import os
import psycopg2
from psycopg2 import Error
from datetime import datetime


def poseDetection(path):
    inputImage = cv2.imread(path)

    # protoFile = r"./pose_d.prototxt"
    # weightsFile = r"./pose_i.caffemodel"
    # print(type(inputImage))
    #TODO: WINDOWS
    protoFile = r"./posedetection/pose_d.prototxt"
    weightsFile = r"./posedetection/pose_i.caffemodel"
    nPoints = 18
    keypointsMapping = ['Nose', 'Neck', 'R-Sho', 'R-Elb', 'R-Wr', 'L-Sho', 'L-Elb', 'L-Wr', 'R-Hip', 'R-Knee', 'R-Ank', 'L-Hip', 'L-Knee', 'L-Ank', 'R-Eye', 'L-Eye', 'R-Ear', 'L-Ear']

    POSE_PAIRS = [[1,2], [1,5], [2,3], [3,4], [5,6], [6,7],
                  [1,8], [8,9], [9,10], [1,11], [11,12], [12,13],
                  [1,0], [0,14], [14,16], [0,15], [15,17],
                  [2,17], [5,16] ]

    #index of pafs correspoding to the POSE_PAIRS
    #e.g for POSE_PAIR(1,2), the PAFs are located at indices (31,32) of output, Similarly, (1,5) -> (39,40) and so on.
    mapIdx = [[31,32], [39,40], [33,34], [35,36], [41,42], [43,44],
              [19,20], [21,22], [23,24], [25,26], [27,28], [29,30],
              [47,48], [49,50], [53,54], [51,52], [55,56],
              [37,38], [45,46]]

    colors = [ [0,100,255], [0,100,255], [0,255,255], [0,100,255], [0,255,255], [0,100,255],
             [0,255,0], [255,200,100], [255,0,255], [0,255,0], [255,200,100], [255,0,255],
             [0,0,255], [255,0,0], [200,200,0], [255,0,0], [200,200,0], [0,0,0]]


    def getKeypoints(probMap, threshold=0.1):
        mapSmooth = cv2.GaussianBlur(probMap,(3,3),0,0)
        mapMask = np.uint8(mapSmooth>threshold)
        keypoints = []
        #find blobs
        contours, _ = cv2.findContours(mapMask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        #find max for each blob
        for contour in contours:
            blobMask = np.zeros(mapMask.shape)
            blobMask = cv2.fillConvexPoly(blobMask, contour, 1)
            maskedProbMap = mapSmooth * blobMask
            _, maxVal, _, maxLoc = cv2.minMaxLoc(maskedProbMap)
            keypoints.append(maxLoc + (probMap[maxLoc[1], maxLoc[0]],))
        return keypoints


    #connect the right joints to create limbs- get valid and invalid connections
    def getValidPairs(output):
        valid_pairs = []
        invalid_pairs = []
        n_interp_samples = 10
        paf_score_th = 0.1
        conf_th = 0.7
        #for every POSE_PAIR
        for k in range(len(mapIdx)):
            #A->B constitute a limb
            pafA = output[0, mapIdx[k][0], :, :]
            pafB = output[0, mapIdx[k][1], :, :]
            pafA = cv2.resize(pafA, (frameWidth, frameHeight))
            pafB = cv2.resize(pafB, (frameWidth, frameHeight))
            #Find keypoints for the first and second limb
            candA = detected_keypoints[POSE_PAIRS[k][0]]
            candB = detected_keypoints[POSE_PAIRS[k][1]]
            nA = len(candA)
            nB = len(candB)
            # If keypoints for the joint-pair is detected
            # check every joint in candA with every joint in candB
            # Calculate the distance vector between the two joints
            # Find the PAF values at a set of interpolated points between the joints
            # Use the above formula to compute a score to mark the connection valid
            if( nA != 0 and nB != 0):
                valid_pair = np.zeros((0,3))
                for i in range(nA):
                    max_j=-1
                    maxScore = -1
                    found = 0
                    for j in range(nB):
                        # Find d_ij
                        d_ij = np.subtract(candB[j][:2], candA[i][:2])
                        norm = np.linalg.norm(d_ij)
                        if norm:
                            d_ij = d_ij / norm
                        else:
                            continue
                        # Find p(u)
                        interp_coord = list(zip(np.linspace(candA[i][0], candB[j][0], num=n_interp_samples),
                                                np.linspace(candA[i][1], candB[j][1], num=n_interp_samples)))
                        # Find L(p(u))
                        paf_interp = []
                        for k in range(len(interp_coord)):
                            paf_interp.append([pafA[int(round(interp_coord[k][1])), int(round(interp_coord[k][0]))],
                                               pafB[int(round(interp_coord[k][1])), int(round(interp_coord[k][0]))] ])
                        # Find E
                        paf_scores = np.dot(paf_interp, d_ij)
                        avg_paf_score = sum(paf_scores)/len(paf_scores)

                        # Check if the connection is valid
                        # If the fraction of interpolated vectors aligned with PAF is higher then threshold -> Valid Pair
                        if ( len(np.where(paf_scores > paf_score_th)[0]) / n_interp_samples ) > conf_th :
                            if avg_paf_score > maxScore:
                                max_j = j
                                maxScore = avg_paf_score
                                found = 1
                    # Append the connection to the list
                    if found:
                        valid_pair = np.append(valid_pair, [[candA[i][3], candB[max_j][3], maxScore]], axis=0)

                # Append the detected connections to the global list
                valid_pairs.append(valid_pair)
            else: # If no keypoints are detected
                #print("No Connection : k = {}".format(k))
                invalid_pairs.append(k)
                valid_pairs.append([])
        return valid_pairs, invalid_pairs


    #creates a list of keypoints belonging to each person
    #for each detected valid pair, it assigns the joint(s) to a person
    def getPersonwiseKeypoints(valid_pairs, invalid_pairs):
        # the last number in each row is the overall score
        personwiseKeypoints = -1 * np.ones((0, 19))

        for k in range(len(mapIdx)):
            if k not in invalid_pairs:
                partAs = valid_pairs[k][:,0]
                partBs = valid_pairs[k][:,1]
                indexA, indexB = np.array(POSE_PAIRS[k])

                for i in range(len(valid_pairs[k])):
                    found = 0
                    person_idx = -1
                    for j in range(len(personwiseKeypoints)):
                        if personwiseKeypoints[j][indexA] == partAs[i]:
                            person_idx = j
                            found = 1
                            break

                    if found:
                        personwiseKeypoints[person_idx][indexB] = partBs[i]
                        personwiseKeypoints[person_idx][-1] += keypoints_list[partBs[i].astype(int), 2] + valid_pairs[k][i][2]

                    # if find no partA in the subset, create a new subset
                    elif not found and k < 17:
                        row = -1 * np.ones(19)
                        row[indexA] = partAs[i]
                        row[indexB] = partBs[i]
                        # add the keypoint_scores for the two keypoints and the paf_score
                        row[-1] = sum(keypoints_list[valid_pairs[k][i,:2].astype(int), 2]) + valid_pairs[k][i][2]
                        personwiseKeypoints = np.vstack([personwiseKeypoints, row])
        return personwiseKeypoints


    frameWidth = inputImage.shape[1] #image width
    frameHeight = inputImage.shape[0] #image height

    #t = time.time()
    net = cv2.dnn.readNetFromCaffe(protoFile, weightsFile)
    # if args.device == "cpu":
    #     net.setPreferableBackend(cv2.dnn.DNN_TARGET_CPU)
    #     #print("Using CPU device")
    # elif args.device == "gpu":
    #     net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
    #     net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA)
    #     #print("Using GPU device")

    #Fix the input Height and get the width according to the Aspect Ratio
    inHeight = 368
    inWidth = int((inHeight/frameHeight)*frameWidth)

    inpBlob = cv2.dnn.blobFromImage(inputImage, 1.0 / 255, (inWidth, inHeight), (0, 0, 0), swapRB=False, crop=False)

    net.setInput(inpBlob)
    output = net.forward()
    #print("Time Taken in forward pass = {}".format(time.time() - t))

    detected_keypoints = []
    keypoints_list = np.zeros((0,3))
    keypoint_id = 0
    threshold = 0.1

    for part in range(nPoints):
        probMap = output[0,part,:,:]
        probMap = cv2.resize(probMap, (inputImage.shape[1], inputImage.shape[0]))
        keypoints = getKeypoints(probMap, threshold)
        #print("Keypoints - {} : {}".format(keypointsMapping[part], keypoints))
        keypoints_with_id = []
        for i in range(len(keypoints)):
            keypoints_with_id.append(keypoints[i] + (keypoint_id,))
            keypoints_list = np.vstack([keypoints_list, keypoints[i]])
            keypoint_id += 1

        detected_keypoints.append(keypoints_with_id)

    resultImage = inputImage.copy()

    valid_pairs, invalid_pairs = getValidPairs(output)
    personwiseKeypoints = getPersonwiseKeypoints(valid_pairs, invalid_pairs)

    for i in range(17):
        for n in range(len(personwiseKeypoints)):
            index = personwiseKeypoints[n][np.array(POSE_PAIRS[i])]
            if -1 in index:
                continue
            B = np.int32(keypoints_list[index.astype(int), 0])
            A = np.int32(keypoints_list[index.astype(int), 1])
            cv2.line(resultImage, (B[0], A[0]), (B[1], A[1]), colors[i], 3, cv2.LINE_AA)
    # print('Im here')
    # cv2.imwrite(save_path,resultImage)
    result = [resultImage, detected_keypoints]
    return result

    # show the image
    def showImage(title, img):
        cv2.imshow(title, img)

    # showImage("Estimated Poses" , resultImage)

    # cv2.waitKey(0)
    # return resultImage

# save the image
def saveImage(name, img, path):
    cv2.imwrite(os.path.join(path, name), img)

def connetDatabase(im, accuracy, precision, F1_score, recall, detected_keypoints, tags, comment, experiment_id):
    # prvih pet argumenata stavljamo u tablicu "image"
    
    # binarni zapis slike
    is_success, im_buf_arr = cv2.imencode(".jpg", im)
    byte_im = im_buf_arr.tobytes()
    #print(byte_im)

    # binarni zapis slike, ali mora biti spremljena u dat sustav --> spremljena je u C:\Users\dolja\Desktop\zavrsni\temp.jpg
    #with open(r"C:\Users\dolja\Desktop\zavrsni\temp.jpg", "rb") as fp:
        #byte_im = fp.read()
    #print(byte_im)

    # datetime object containing current date and time
    now = datetime.now()
    #print("Now:", now)
    dt_string = now.strftime("%d/%m/%Y %H:%M:%S") # dd/mm/YY H:M:S
    #print("date and time:", dt_string)
    
    try:
        # Connect to an existing database
        connection = psycopg2.connect(user="postgres",
                                    password="bazepodataka",
                                    host="localhost",
                                    port="5432",
                                    database="poseDetection")

        # Create a cursor to perform database operations
        cursor = connection.cursor()

        create_table_image_query = '''CREATE TABLE IF NOT EXISTS image
            (id SERIAL PRIMARY KEY NOT NULL,
            img_binary bytea NOT NULL,
            created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP(0),
            accuracy REAL,
            precision REAL,
            recall REAL,
            F1_score REAL,
            comment VARCHAR(255),
            experiment_id INTEGER); '''
        create_table_keypoint_query = '''CREATE TABLE IF NOT EXISTS keypoint
            (id SMALLINT PRIMARY KEY NOT NULL,
            name VARCHAR(20) NOT NULL); '''
        create_table_detected_keypoint_query = '''CREATE TABLE IF NOT EXISTS keypoint_image
            (img_id INTEGER NOT NULL,
            keypoint_id SMALLINT NOT NULL,
            x_coordinate SMALLINT,
            y_coordinate SMALLINT,
            tag CHAR(2) NOT NULL,
            PRIMARY KEY(img_id, keypoint_id),
			CONSTRAINT fk_image
			  FOREIGN KEY(img_id) 
			  REFERENCES image(id)
			  ON DELETE CASCADE,
			CONSTRAINT fk_keypoint
			  FOREIGN KEY(keypoint_id) 
			  REFERENCES keypoint(id)
			  ON DELETE CASCADE
			); '''
        insert_values_into_table_keypoint = '''
            INSERT INTO 
                keypoint (id, name)
            VALUES
                (0, 'Nose'), (1, 'Neck'), (2, 'Right Shoulder'), (3, 'Right Elbow'), (4, 'Right Wrist'),
                (5, 'Left Shoulder'), (6, 'Left Elbow'), (7, 'Left Wrist'), (8, 'Right Hip'),
                (9, 'Right Knee'), (10, 'Right Ankle'), (11, 'Left Hip'), (12, 'Left Knee'),
                (13, 'Left Ankle'), (14, 'Right Eye'), (15, 'Left Eye'), (16, 'Right Ear'), (17, 'Left Ear');'''
        
        # Create table "image"
        cursor.execute(create_table_image_query)
        connection.commit()
        # Create table "keypoint"
        cursor.execute(create_table_keypoint_query)
        connection.commit()
        # Create table "detected_keypoint"
        cursor.execute(create_table_detected_keypoint_query)
        connection.commit()
        # zapis podataka o obrađenoj slici u tablicu image
        cursor.execute("INSERT INTO image (id, img_binary, created_at, accuracy, precision, recall, F1_score, comment, experiment_id) VALUES ( DEFAULT, %s::bytea , DEFAULT , %s , %s , %s , %s , NULLIF(%s, '') , NULLIF(%s, '')::integer ) RETURNING id", (byte_im, str(accuracy), str(precision), str(recall), str(F1_score), comment, experiment_id))
        returned_id = cursor.fetchone()[0] #upravo generirani SERIAL id od slike u tablici image
        #print("Upravo generirani SERIAL id od slike u tablici image: " + str(returned_id))
        connection.commit()
        
        cursor.execute("SELECT img_binary FROM image WHERE id = %s", [returned_id] )
        result = cursor.fetchone()
        
        #print( "Dohvaceni binarni zapis slike s id-jem *{0}* : {1}".format( str(returned_id), bytes(result[0]) ) )
        connection.commit()
        '''
        try:
            cursor.execute(insert_values_into_table_keypoint)
            connection.commit()
        except:
            print("The data already exists.")
        '''
        try:
            for i in range(len(detected_keypoints)):
                # zapis podataka o svakom keypointu s upravo obrađene slike u tablicu keypoint_image
                if(len(detected_keypoints[i]) > 0):
                    #print("INSERT INTO keypoint_image (img_id, keypoint_id, x_coordinate, y_coordinate, tag) VALUES ( {0}, {1}, {2}, {3}, '{4}' )".format(returned_id, i, detected_keypoints[i][0][0], detected_keypoints[i][0][1], tags[i]) )
                    cursor.execute("INSERT INTO keypoint_image (img_id, keypoint_id, x_coordinate, y_coordinate, tag) VALUES ( {0}, {1}, {2}, {3}, '{4}' )".format(returned_id, i, detected_keypoints[i][0][0], detected_keypoints[i][0][1], tags[i]) )
                else:
                    #print("INSERT INTO keypoint_image (img_id, keypoint_id, x_coordinate, y_coordinate, tag) VALUES ( {0}, {1}, NULL, NULL, '{2}' )".format(returned_id, i, tags[i]) )
                    cursor.execute("INSERT INTO keypoint_image (img_id, keypoint_id, x_coordinate, y_coordinate, tag) VALUES ( {0}, {1}, NULL, NULL, '{2}' )".format(returned_id, i, tags[i]) )
                #cursor.execute("INSERT INTO keypoint_image (img_id, keypoint_id, x_coordinate, y_coordinate, tag) VALUES ( %s, %s, %s, %s, %s )", (returned_id, i, detected_keypoints[i][0][0], detected_keypoints[i][0][1], tags[i]) )
                connection.commit()
        except (Exception, Error) as error:
            print("Error while saving keypoints data to database: ", error)

        print("The data has been successfully sent to PostgreSQL database.")
    except (Exception, Error) as error:
        print("Error while connecting to PostgreSQL: ", error)
    finally:
        if (connection):
            cursor.close()
            connection.close()
            print("PostgreSQL connection is closed")

def fetchFromDatabase(experiment_id, image_id):
    try:
        # Connect to an existing database
        connection = psycopg2.connect(user="postgres",
                                    password="bazepodataka",
                                    host="localhost",
                                    port="5432",
                                    database="poseDetection")

        # Create a cursor to perform database operations
        cursor = connection.cursor()
        if(len(experiment_id) == 0):
            cursor.execute("SELECT * FROM image WHERE id = %s", [image_id] )
            result = cursor.fetchall()
            connection.commit()
            cursor.execute("SELECT AVG(accuracy), AVG(precision), AVG(recall), AVG(F1_score) FROM image WHERE id = %s", [image_id] )
            averages = cursor.fetchall()
            connection.commit()
        elif(len(image_id) == 0):
            cursor.execute("SELECT * FROM image WHERE experiment_id = %s", [experiment_id] )
            result = cursor.fetchall()
            connection.commit()
            cursor.execute("SELECT AVG(accuracy), AVG(precision), AVG(recall), AVG(F1_score) FROM image WHERE experiment_id = %s", [experiment_id] )
            averages = cursor.fetchall()
            connection.commit()
        else:
            cursor.execute("SELECT * FROM image WHERE id = %s AND experiment_id = %s", [image_id, experiment_id] )
            result = cursor.fetchall()
            connection.commit()
            cursor.execute("SELECT AVG(accuracy), AVG(precision), AVG(recall), AVG(F1_score) FROM image WHERE id = %s AND experiment_id = %s", [image_id, experiment_id] )
            averages = cursor.fetchall()
            connection.commit()
        return [result, averages]
    except (Exception, Error) as error:
        print("Error while connecting to PostgreSQL: ", error)
    finally:
        if (connection):
            cursor.close()
            connection.close()
            print("PostgreSQL connection is closed")

#resultImage = poseDetection(r"C:\Users\dolja\Downloads\pose.jpg")
#resultImage = poseDetection(r"C:\Users\dolja\Desktop\zavrsni\posedetection\woman.jpg")[0]
#im = cv2.imread(r"C:\Users\dolja\Desktop\zavrsni\posedetection\woman.jpg")
#im = poseDetection(r"C:\Users\dolja\Desktop\zavrsni\posedetection\woman.jpg")[0]
#cv2.imshow("result" , im)
#cv2.waitKey(0); # wait forever
#saveImage("estimatedPoses.jpg", resultImage, 'D:/OpenCV/Scripts/Images')
